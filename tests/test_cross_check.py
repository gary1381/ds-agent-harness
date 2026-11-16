"""H v0.4 T2: a host-side second opinion lifts a held answer to "cross-checked" (internal use) when a second,
different model answers the same question with the same numbers (docs/experiments/H_v0.4_PLAN.md)."""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from dsharness import tools as T
from dsharness.common import read_jsonl
from dsharness.evidence import decide
from dsharness.evidence.gate import load_policy
from dsharness.tools import analytics as A
from test_answer_v02 import private_ws

ROOT = Path(__file__).resolve().parents[1]
CERT_SCHEMA = json.loads((ROOT / "docs" / "answer_certificate.schema.json").read_text())
Q = "How many customers signed up in each month of 2025?"
SIGNUPS = """
SELECT strftime(created_at, '%Y-%m') AS month, COUNT(*) AS value FROM silver.customers
WHERE NOT is_test_account AND created_at >= TIMESTAMP '2025-01-01' AND created_at < TIMESTAMP '2026-01-01'
GROUP BY 1 ORDER BY 1"""
SIGNUPS_OTHER_SQL = """
SELECT substr(CAST(created_at AS VARCHAR), 1, 7) AS Month, count(customer_id) AS VALUE
FROM silver.customers WHERE is_test_account = false AND year(created_at) = 2025 GROUP BY 1"""
SIGNUPS_SHIFTED = SIGNUPS.replace("strftime(created_at, '%Y-%m')", "strftime(created_at + INTERVAL 15 DAY, '%Y-%m')")


@pytest.fixture(scope="module")
def ws(small_ws, tmp_path_factory):
    return private_ws(small_ws, tmp_path_factory.mktemp("t2") / "ws")


@pytest.fixture(scope="module")
def agents(ws):
    ctxs = {name: T.make_context(ws, "score", "analyst", f"agent:{name}") for name in ("claude-1", "Claude-1", "codex-1")}
    host = T.make_context(ws, "score", "analyst", "host:dsh")
    yield ctxs, host
    for c in (*ctxs.values(), host):
        c.close()


def answer(ctx, sql, question=Q):
    rows = T.run_sql(ctx, sql)["rows"]
    r = T.submit_answer(ctx, question, rows, sql=sql)
    return {"certificate_id": r["certificate"]["certificate_id"], "answer_rows": rows}, r


def test_agreeing_second_model_lifts_a_held_answer_to_t2(ws, agents):
    (ctxs, host) = agents
    first, r1 = answer(ctxs["claude-1"], SIGNUPS)
    second, _ = answer(ctxs["codex-1"], SIGNUPS_OTHER_SQL)
    assert (r1["tier"], r1["trust_label"], r1["gate"]["verdict"]) == ("CONSISTENT", "T3", "HOLD")
    out = A.cross_check_answers(host, {**first, "model": "claude-sonnet-5-5", "provider": "anthropic"},
                                {**second, "model": "gpt-6-luna", "provider": "openai"})
    assert out["outcome"] == "PASS" and out["problems"] == [], out
    assert out["comparison"]["key_columns"] == ["month"] and out["comparison"]["n_rows"] == len(first["answer_rows"])
    assert out["trust_label"] == "T2" and out["gate"]["verdict"] == "ALLOW" and "internal" in out["allowed_use"]
    cert = out["certificate"]
    jsonschema.Draft202012Validator(CERT_SCHEMA).validate(cert)
    assert cert["certificate_id"] == A.certificate_id(cert)
    assert (cert["tier"], cert["gate"]["stage"], cert["producer"]) == ("CONSISTENT", "answer_internal",
                                                                       "dsharness.tools.cross_check_answers")
    assert cert["second_opinion"] == {
        "certificate_id": second["certificate_id"], "second_requested_by": "agent:codex-1",
        "first_model": "claude-sonnet-5-5", "first_provider": "anthropic", "second_model": "gpt-6-luna",
        "second_provider": "openai", "outcome": "PASS", "evidence_id": out["evidence_id"], "requested_by": "host:dsh"}
    assert cert["checks"][-1]["check_id"] == "answer.cross_check" and cert["requested_by"] == "agent:claude-1"
    v = jsonschema.Draft202012Validator(CERT_SCHEMA)          # T2 needs the passing cross-check on the certificate
    assert not v.is_valid({**cert, "checks": cert["checks"][:-1] + [{**cert["checks"][-1], "outcome": "FAIL"}]})
    assert not v.is_valid({k: x for k, x in cert.items() if k != "second_opinion"})
    assert [c for c in read_jsonl(ws.registry / A.CERTIFICATES_LOG) if c["certificate_id"] == cert["certificate_id"]] == [cert]
    # T2 is internal use only: the official gate for the same answer still holds it
    subject = next(r.subject for r in A.EvidenceStore(ws).all() if r.evidence_id == out["evidence_id"])
    assert decide(ws, "answer_publish", subject, requested_by="host:dsh").verdict == "HOLD"


def test_disagreement_holds_for_a_person_with_the_differences(ws, agents):
    (ctxs, host) = agents
    first, _ = answer(ctxs["claude-1"], SIGNUPS)
    second, _ = answer(ctxs["codex-1"], SIGNUPS_SHIFTED)            # a month-boundary error (E1's Codex error)
    out = A.cross_check_answers(host, {**first, "model": "claude", "provider": "anthropic"},
                                {**second, "model": "codex", "provider": "openai"})
    assert out["outcome"] == "FAIL" and out["comparison"]["n_mismatches"] > 0
    m = out["comparison"]["mismatches"][0]
    assert set(m) == {"key", "column", "first", "second"} and m["second"] != m["first"]
    assert out["trust_label"] == "T3" and out["gate"]["verdict"] == "HOLD"        # HOLD, not REJECT: either may be right
    assert out["certificate"]["second_opinion"]["outcome"] == "FAIL"
    assert "disagrees" in out["certificate"]["reasons"][0]
    jsonschema.Draft202012Validator(CERT_SCHEMA).validate(out["certificate"])


@pytest.mark.parametrize("case", ["same_agent", "same_agent_other_case", "same_provider", "unnamed_provider",
                                  "other_question"])
def test_a_second_opinion_needs_a_second_agent_model_and_the_same_question(ws, agents, case):
    (ctxs, host) = agents
    first, _ = answer(ctxs["claude-1"], SIGNUPS)
    other_ctx = {"same_agent": ctxs["claude-1"], "same_agent_other_case": ctxs["Claude-1"]}.get(case, ctxs["codex-1"])
    second, _ = answer(other_ctx, SIGNUPS, question=Q + " (by month)" if case == "other_question" else Q)
    providers = {"same_provider": ("openai", "OpenAI"), "unnamed_provider": ("anthropic", "")}.get(case, ("anthropic", "openai"))
    out = A.cross_check_answers(host, {**first, "model": "m1", "provider": providers[0]},
                                {**second, "model": "m2", "provider": providers[1]})
    assert out["outcome"] == "INCONCLUSIVE" and out["gate"]["verdict"] == "HOLD" and out["trust_label"] == "T3"
    assert len(out["problems"]) == 1, out["problems"]
    jsonschema.Draft202012Validator(CERT_SCHEMA).validate(out["certificate"])


def test_an_answer_the_governed_metric_contradicts_is_never_lifted(ws, agents):
    (ctxs, host) = agents
    q = "What was net revenue by region for March 2026?"
    rows = T.query_metric(ctxs["claude-1"], "net_revenue_fen", ["region"], ["2026-03", "2026-03"])["rows"]
    call = {"metric": "net_revenue_fen", "dimensions": ["region"], "months": ["2026-03", "2026-03"], "filters": {}}
    wrong = [{**r, "value": r["value"] + 1000} for r in rows]
    r1 = T.submit_answer(ctxs["claude-1"], q, wrong, metric_call=call)
    r2 = T.submit_answer(ctxs["codex-1"], q, wrong, metric_call=call)
    assert r1["tier"] == "FLAGGED"
    out = A.cross_check_answers(host, {"certificate_id": r1["certificate"]["certificate_id"], "answer_rows": wrong,
                                       "model": "claude", "provider": "anthropic"},
                                {"certificate_id": r2["certificate"]["certificate_id"], "answer_rows": wrong,
                                 "model": "codex", "provider": "openai"})
    assert out["outcome"] == "INCONCLUSIVE" and out["trust_label"] is None
    assert out["gate"]["verdict"] == "REJECT"                  # its own re-execution against the metric failed
    assert any("governed check contradicts" in p for p in out["problems"])


def test_rows_must_be_the_certified_rows(ws, agents):
    (ctxs, host) = agents
    first, _ = answer(ctxs["claude-1"], SIGNUPS)
    second, _ = answer(ctxs["codex-1"], SIGNUPS)
    tampered = [{**r, "value": r["value"] + 1} for r in second["answer_rows"]]
    a, b = {**first, "model": "a", "provider": "p"}, {**second, "model": "b", "provider": "q"}
    with pytest.raises(ValueError, match="not the rows"):
        A.cross_check_answers(host, a, {**b, "answer_rows": tampered})
    with pytest.raises(ValueError, match="no certificate"):
        A.cross_check_answers(host, a, {**b, "certificate_id": "ac-" + "0" * 24})
    t2 = A.cross_check_answers(host, a, b)["certificate"]           # a cross-check certificate is not an answer
    with pytest.raises(ValueError, match="not an answer certificate"):
        A.cross_check_answers(host, a, {**b, "certificate_id": t2["certificate_id"]})


def test_a_verified_answer_needs_no_second_opinion(ws, agents):
    (ctxs, host) = agents
    q = "What was net revenue by region for March 2026?"
    call = {"metric": "net_revenue_fen", "dimensions": ["region"], "months": ["2026-03", "2026-03"], "filters": {}}
    rows = T.query_metric(ctxs["claude-1"], "net_revenue_fen", ["region"], ["2026-03", "2026-03"])["rows"]
    r1 = T.submit_answer(ctxs["claude-1"], q, rows, metric_call=call)
    r2 = T.submit_answer(ctxs["codex-1"], q, rows, metric_call=call)
    assert r1["tier"] == "VERIFIED"
    with pytest.raises(ValueError, match="already VERIFIED"):
        A.cross_check_answers(host, {"certificate_id": r1["certificate"]["certificate_id"], "answer_rows": rows,
                                     "model": "a", "provider": "p"},
                              {"certificate_id": r2["certificate"]["certificate_id"], "answer_rows": rows,
                               "model": "b", "provider": "q"})


def test_cross_check_is_not_an_agent_tool():
    assert "cross_check_answers" not in T.TOOL_REGISTRY
    assert all("cross_check_answers" not in tools for tools in T.ROLE_TOOLS.values())
    policy = load_policy()
    assert policy["stages"]["answer_internal"]["requires"] == ["answer.sql_static", "answer.reexecution",
                                                               "answer.cross_check"]
    assert policy["checks"]["answer.cross_check"]["allowed_producers"] == ["verifier:cross_check"]


@pytest.mark.parametrize("a, b, same", [
    (611761, 611761.0, True),                 # a retyped integer
    (0.0580497569, 0.05805, True),            # rounded within half a unit
    (123, 123.4, True),                       # the coarser rounding sets the tolerance
    (5208.785904649097, 5211.872592592593, False),   # 0.06% apart: over 0.05%
    (105.39, 105.45, False),
    (0.58, 0.5808, False),                    # two significant digits do not count unless equal
    (0.58, 0.58, True),
    (42, 43, False),
    (10000, 9995, False),                     # whole numbers (counts, ids) must be equal
    (123456, 123457, False),
    (1234.5, 1235.0, True),                   # a fraction on either side: the tolerance applies
])
def test_values_close_is_symmetric_and_uses_the_census_tolerance(a, b, same):
    assert A.values_close(a, b) is same and A.values_close(b, a) is same


def test_compare_answers_pairs_rows_by_key_whatever_the_order_and_case():
    a = [{"Month": "2025-01", "value": 10.0}, {"Month": "2025-02", "value": 12.5}]
    b = [{"month": "2025-02", "VALUE": 12.5}, {"month": "2025-01", "value": 10}]
    assert A.compare_answers(a, b)["outcome"] == "PASS"
    assert A.compare_answers(a, b[:1])["outcome"] == "FAIL"
    assert A.compare_answers(a, b[:1])["only_in_first"] == [{"month": "2025-01"}]
    assert A.compare_answers(a, [{"month": "2025-01", "total": 10}])["outcome"] == "INCONCLUSIVE"
    years = [{"year": 2024, "value": 1.5}, {"year": 2025, "value": 2.5}]       # a numeric key named as one
    assert A.compare_answers(years, list(reversed(years)))["outcome"] == "PASS"
    assert A.compare_answers([{"value": 1}, {"value": 1}], [{"value": 1}])["outcome"] == "INCONCLUSIVE"
    assert A.compare_answers([], [{"value": 1}])["outcome"] == "INCONCLUSIVE"
    assert A.compare_answers([{"Revenue": 5, "revenue": 7}], [{"revenue": 7}])["outcome"] == "INCONCLUSIVE"
    ids = [{"month": "2025-01", "top_customer_id": 123456, "revenue": 1000.0}]
    assert A.compare_answers(ids, [{**ids[0], "top_customer_id": 123457}])["outcome"] == "FAIL"


def test_compare_answers_sets_aside_only_what_the_second_answer_adds():
    one = [{"value": 0.4125}]
    labelled = [{"month": "2025-03", "value": 0.4125}]                       # the month asked about, as a label
    cmp = A.compare_answers(one, labelled)
    assert cmp["outcome"] == "PASS" and cmp["second_columns_set_aside"] == ["month"]
    assert A.compare_answers(labelled, one)["outcome"] == "INCONCLUSIVE"     # the delivered label is unconfirmed
    assert A.compare_answers(one, [{"month": "2025-03", "value": 0.4130}])["outcome"] == "FAIL"
    helper = [{"plan": "pro", "value": 12.5, "tickets": 40, "subscribers": 320},
              {"plan": "basic", "value": 3.25, "tickets": 13, "subscribers": 400}]
    plain = [{"plan": "basic", "value": 3.25}, {"plan": "pro", "value": 12.5}]
    assert A.compare_answers(plain, helper)["outcome"] == "PASS"
    assert A.compare_answers(helper, plain)["outcome"] == "INCONCLUSIVE"
    monthly = [{"month": "2025-01", "value": 10.0}, {"month": "2025-02", "value": 12.5}]
    assert A.compare_answers([{"value": 22.5}], monthly)["outcome"] == "INCONCLUSIVE"   # a key that splits rows
    split = [{"region_id": 1, "value": 5.5}, {"region_id": 2, "value": 5.5}]   # an unhinted numeric key
    assert A.compare_answers([{"value": 5.5}], split)["outcome"] == "INCONCLUSIVE"
