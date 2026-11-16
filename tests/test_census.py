"""E1 census summary: pre-registered metrics on synthetic attempt records."""
from __future__ import annotations

from dsharness.bench.census import summarize


def rec(agent, arm, qid, rep, correct, delivered=True):
    return {"agent": agent, "arm": arm, "qid": qid, "rep": rep, "status": "OK", "correct": correct,
            "delivered": delivered, "claimed_success": delivered}


def test_pooled_pass_hat_3_is_the_mean_over_agent_question_cells():
    rs = [rec("claude", "N", "q1", k, True) for k in (1, 2, 3)]
    rs += [rec("codex", "N", "q1", k, k != 2) for k in (1, 2, 3)]
    rs += [rec(a, "N", "q2", k, True) for a in ("claude", "codex") for k in (1, 2, 3)]
    s = summarize(rs, boot=50)
    assert s["claude/N"]["M4_pass_hat_3"] == 1.0
    assert s["codex/N"]["M4_pass_hat_3"] == 0.5
    assert s["pooled/N"]["M4_pass_hat_3"] == 0.75           # 3 of 4 (agent, question) cells pass 3/3
    assert s["pooled/N"]["questions"] == 2


def test_silent_errors_and_false_holds():
    rs = [rec("claude", "H", "q1", 1, False, True), rec("claude", "H", "q1", 2, True, False),
          rec("claude", "H", "q1", 3, True, True), rec("claude", "N", "q1", 1, False, True),
          rec("claude", "N", "q1", 2, True, True), rec("claude", "N", "q1", 3, True, True)]
    s = summarize(rs, boot=50)
    assert s["claude/H"]["M1_silent_error_rate"] == 1 / 3 and s["claude/H"]["M2_false_hold_rate"] == 0.5
    assert s["claude/N"]["M2_false_hold_rate"] == 0.0
    assert s["claude/diff_M1_H_minus_N"]["point"] == 0.0


def test_three_arm_paired_differences():
    rs = [rec("claude", arm, q, k, ok, True) for q in ("q1", "q2") for k in (1, 2, 3)
          for arm, ok in (("N", False), ("H", True), ("H1", False))]
    s = summarize(rs, boot=50)
    assert s["claude/diff_M1_H_minus_N"]["point"] == -1.0
    assert s["claude/diff_M1_H1_minus_N"]["point"] == 0.0
    assert s["claude/diff_M1_H_minus_H1"]["point"] == -1.0
    assert "claude/H1-claimed" in s


def test_arm_h1_needs_the_frozen_source(tmp_path):
    import pytest
    from dsharness.bench.census import Census, CensusError
    with pytest.raises(CensusError, match="h1_src"):
        Census(None, tmp_path, [], [], ["N", "H1"], 3)
    with pytest.raises(CensusError, match="h1_src"):
        Census(None, tmp_path, [], [], ["H1"], 3, h1_src=tmp_path)


def test_subscription_world_prompts_are_unchanged():
    """The E1 / E1b agent prompts, rendered through the World config, are byte-identical (sha256 pinned)."""
    import hashlib

    from dsharness.bench import census as C
    w = C.SUBSCRIPTION
    n = C.PROMPT_N.format(company=w.company, key_example=w.key_example, question="QQ")
    h = C.PROMPT_H.format(company=w.company, server=C.SERVER, question="QQ")
    assert hashlib.sha256(n.encode()).hexdigest() == "04716dfa130e19e6402e5d6213e75e78e3ec980f23bb214e57fda459e4abb7be"
    assert hashlib.sha256(h.encode()).hexdigest() == "7d265a541e70fdccd48a9539bec45fe83bdb85037d68ac132cb38f44d4d799b2"


def test_key_spellings_follow_the_world():
    from dsharness.bench import census as C
    q = C.Question("q", "C", "x", "en", "?", ("market",), "other", ("2025-01", "2025-01"))
    truth = {"uk": 10.0, "non_uk": 5.0}
    rows = [{"market": "United Kingdom", "value": 10}, {"market": "Non-UK", "value": 5}]
    assert not C.grade_values(q, rows, truth)["correct"]                    # subscription spellings
    retail = {**C.KEY_ALIASES, "united_kingdom": "uk", "英国": "uk"}
    assert C.grade_values(q, rows, truth, retail)["correct"]
    assert C.grade_values(q, [{"market": "英国", "value": 10}, {"market": "non-uk", "value": 5}], truth, retail)["correct"]


def test_arm_n_warehouse_keeps_the_raw_export_readable(small_ws, tmp_path):
    """bronze_safe.* are views over bronze.*; the copy for arm N drops bronze, so the views must become tables."""
    import duckdb

    from dsharness.bench.census import prepare_warehouse_copy
    path = prepare_warehouse_copy(small_ws, tmp_path / "wh.duckdb")
    con = duckdb.connect(str(path), read_only=True)
    try:
        schemas = {s for (s,) in con.execute("SELECT DISTINCT schema_name FROM duckdb_tables()").fetchall()}
        assert "bronze" not in schemas and "bronze_safe" in schemas
        assert con.execute("SELECT count(*) FROM duckdb_views() WHERE NOT internal").fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM bronze_safe.billing_invoices").fetchone()[0] > 0
    finally:
        con.close()


def test_e4_benchmark_fixes_are_off_unless_the_world_turns_them_on():
    """B2: item_code for sku and a quarter label for a month are right only in a world that says so (E4)."""
    from dsharness.bench import census as C
    q = C.Question("q", "R", "x", "en", "?", ("sku",), "count", ("2025-01", "2025-03"))
    truth = {"85123a": 10.0, "22423": 7.0}
    rows = [{"item_code": "85123A", "month": "2025-Q1", "value": 10}, {"item_code": "22423", "month": "2025-Q1", "value": 7}]
    assert not C.grade_values(q, rows, truth)["correct"]                       # E1-E3a: unchanged
    assert not C.grade_values(q, rows, truth, None, {"item_code": "sku"})["correct"]   # quarter still outside
    assert C.grade_values(q, rows, truth, None, {"item_code": "sku"}, True)["correct"]
    q4 = C.Question("q", "R", "x", "en", "?", (), "gbp", ("2025-01", "2025-02"))
    assert not C.grade_values(q4, [{"month": "2025-Q1", "value": 3}], {"": 3.0}, None, None, True)["correct"]
    both = [{"item_code": "85123A", "sku": "X", "value": 10}]                  # the bank's own column wins
    assert C.answer_map(q, both, None, {"item_code": "sku"})[0] == {"x": 10}


def test_e4_prompt_names_the_key_columns():
    from dataclasses import replace

    from dsharness.bench import census as C
    q = C.Question("q", "R", "x", "en", "Top 5 items?", ("sku",), "count", ("2025-01", "2025-03"))
    w = replace(C.SUBSCRIPTION, name_keys=True)
    c = C.Census.__new__(C.Census)
    c.world = C.SUBSCRIPTION
    assert c._question_text(q) == "Top 5 items?"
    c.world = w
    assert c._question_text(q).startswith("Top 5 items?\n\n(Answer table: one row per sku, with the key column \"sku\"")
    assert "a single row with no key columns" in C.answer_table_note(replace(q, keys=()))


def test_second_opinions_from_the_other_agents_arm_n_attempts(tmp_path):
    """T2f: a held arm-H answer is lifted by the other agent's arm-N answer only when its own certificate allows
    it; arm N's two-agent baseline needs the agent's claim and the other agent's agreement."""
    import json

    from dsharness.bench import census as C
    from dsharness.common import digest, values_digest
    right, wrong = [{"market": "UK", "value": 10.0}], [{"market": "UK", "value": 12.0}]

    def att(agent, arm, rep, rows, delivered=False, checks=None, tier="CONSISTENT"):
        r = {"agent": agent, "arm": arm, "qid": "q1", "rep": rep, "delivered": delivered,
             "final_answer": {"answer_rows": rows}}
        if checks is not None:
            reg = tmp_path / "attempts" / agent / arm / f"q1-r{rep}" / "harness_ws" / "registry"
            reg.mkdir(parents=True)
            sub = {"principal": C.attempt_principal(agent, arm, "q1", rep), "tool": "submit_answer", "outcome": "ok",
                   "args": {"answer_rows": {"sha256": digest(rows), "values_sha256": values_digest(rows)}}}
            (reg / "audit.jsonl").write_text(json.dumps(sub) + "\n")
            cert = {"answer": {"rows_digest": digest(rows)}, "tier": tier,
                    "checks": [{"check_id": k, "outcome": v} for k, v in checks.items()]}
            (reg / "answer_certificates.jsonl").write_text(json.dumps(cert) + "\n")
        return r
    ok = {"answer.sql_static": "PASS", "answer.reexecution": "PASS", "answer.reconcile": "INCONCLUSIVE"}
    recs = [att("claude", "H", 1, right, checks=ok),                                  # eligible, held
            att("claude", "H", 2, right, checks={**ok, "answer.reconcile": "FAIL"}),  # contradicted: stays held
            att("claude", "H", 3, right),                                             # final rows never submitted
            att("claude", "N", 1, right, delivered=True), att("claude", "N", 2, right, delivered=False),
            att("codex", "N", 1, right, delivered=True), att("codex", "N", 2, wrong, delivered=True)]
    so = C.second_opinions(recs, tmp_path, {"market"})
    assert so[("claude", "H", "q1", 1)] == {"t2n": 0.5, "asks": True, "missing": False}   # 1 of codex's 2 agrees
    assert so[("claude", "H", "q1", 2)] == {"t2n": 0.0, "asks": False, "missing": False}
    assert so[("claude", "H", "q1", 3)] == {"t2n": 0.0, "asks": False, "missing": False}
    assert so[("claude", "N", "q1", 1)] == {"pair": 0.5, "missing": False}
    assert so[("claude", "N", "q1", 2)] == {"pair": 0.0, "missing": False}                # no claim, no delivery
    assert so[("codex", "N", "q1", 2)] == {"pair": 0.0, "missing": False}
    alone = C.second_opinions([r for r in recs if r["agent"] == "claude"], tmp_path, {"market"})
    assert alone[("claude", "H", "q1", 1)] == {"t2n": 0.0, "asks": True, "missing": True}  # no second opinion:
    assert alone[("claude", "N", "q1", 1)] == {"pair": 0.0, "missing": True}               # not delivered
