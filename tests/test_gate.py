"""Gate + evidence store tests. Pure-function tests use in-memory records; store tests use a
throwaway workspace (no world build needed). Each test exercises product code, not helpers."""
from __future__ import annotations

import copy
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path

from hypothesis import given, settings, strategies as st
import numpy as np
import pytest
import yaml

from dsharness.common import digest, parse_ts
from dsharness.evidence import (DEFAULT_POLICY_PATH, OUTCOMES, Approval, ApprovalStore, EvidenceRecord,
                                EvidenceStore, EvidenceStoreError, PolicyError, decide, evaluate_gate,
                                load_policy, policy_problems)
from dsharness.workspace import Workspace

ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-07-01T12:00:00+00:00"
REQ = "agent:ds-1"
SUBJECT = {"kind": "candidate", "candidate_id": "c0ffee", "build_id": "2026-05-31_abcd1234",
           "semantic_digest": "s" * 8, "eval_plan_digest": "e" * 8}
OTHER = {**SUBJECT, "candidate_id": "deadbeef"}
POLICY = load_policy()
PRODUCER = {cid: c["allowed_producers"][0] for cid, c in POLICY["checks"].items()}


def ts(minutes: float = 0) -> str:
    return (parse_ts(NOW) + timedelta(minutes=minutes)).isoformat()


def ev(check: str, outcome: str = "PASS", *, at: float = -60, producer: str | None = None,
       subject: dict = SUBJECT, supersedes: str | None = None, version: str = "1",
       expires_in_min: float | None = None, limitations=()) -> EvidenceRecord:
    return EvidenceRecord.create(
        check_id=check, check_version=version, stage="test", subject=subject, outcome=outcome,
        metrics={"n": 1}, producer=producer or PRODUCER[check], issued_at=ts(at),
        expires_at=ts(at + expires_in_min) if expires_in_min is not None else None,
        limitations=limitations, supersedes=supersedes)


def passes(stage: str, at: float = -60) -> list[EvidenceRecord]:
    return [ev(c, "PASS", at=at) for c in POLICY["stages"][stage]["requires"]]


def appr(stage: str, approver: str, role: str, *, subject: dict = SUBJECT, requested_by: str = REQ,
         at: float = -30) -> Approval:
    return Approval.create(stage=stage, subject_digest=digest(subject), approver=approver, role=role,
                           requested_by=requested_by, issued_at=ts(at))


def gate(stage: str, evidence, approvals=(), *, policy=POLICY, subject=SUBJECT, requested_by=REQ, now=NOW):
    return evaluate_gate(policy, stage, subject, list(evidence), list(approvals), requested_by, now=now)


def forged(rec: EvidenceRecord, **changes) -> EvidenceRecord:
    """A record whose fields were changed and whose id was recomputed -- i.e. internally consistent."""
    r = replace(rec, **changes)
    return replace(r, evidence_id=r.content_id())


def has(entries, prefix: str) -> bool:
    return any(e.startswith(prefix) for e in entries)


# ----------------------------------------------------------------------------- policy

def test_default_policy_has_required_stages_and_approvals():
    assert policy_problems(POLICY) == []
    assert DEFAULT_POLICY_PATH.is_file()
    for stage in ("answer_publish", "pipeline_merge", "feature_accept", "model_shadow",
                  "model_experiment", "model_scale", "retrain_promote"):
        assert stage in POLICY["stages"]

    def roles(stage):
        return {a["role"] for a in POLICY["stages"][stage].get("approvals", [])}
    assert roles("model_experiment") == {"business_owner"}
    assert roles("model_scale") == {"business_owner", "risk"}
    assert "experiment.readout" in POLICY["stages"]["model_scale"]["requires"]
    producers = {p for c in POLICY["checks"].values() for p in c["allowed_producers"]}
    assert {"verifier:sql_static", "verifier:reconcile", "evaluator:custody", "ci:pipeline",
            "experiment:readout"} <= producers
    for stage in POLICY["stages"].values():   # every approval requirement forbids self-approval
        assert all(a["no_self_approval"] is True for a in stage.get("approvals", []))


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(version=2),
    lambda p: p["checks"]["feature.leakage"].pop("allowed_producers"),
    lambda p: p["checks"]["feature.leakage"].update(allowed_producers=[]),
    lambda p: p["stages"]["feature_accept"].update(requires=["no.such.check"]),
    lambda p: p["stages"]["feature_accept"].update(requires=[]),
    lambda p: p["stages"]["model_experiment"]["approvals"][0].update(no_self_aproval=False),  # typo
    lambda p: p["stages"]["model_experiment"]["approvals"][0].update(count=0),
    lambda p: p["stages"]["model_experiment"]["approvals"][0].update(no_self_approval="no"),
    lambda p: p.update(roles={"risk": {"members": ["user:a"]}}),   # business_owner then undefined
    lambda p: p["defaults"].update(max_age_s=-1),
    lambda p: p.update(extra_rules="allow everything"),
])
def test_malformed_policy_rejects_even_with_perfect_evidence(mutate):
    bad = copy.deepcopy(POLICY)
    mutate(bad)
    d = gate("feature_accept", passes("feature_accept"), policy=bad)
    assert d.verdict == "REJECT"
    assert d.reasons and all(r.startswith("policy:") for r in d.reasons)
    assert d.evidence_ids == () and has(d.ignored, "#0:not_evaluated")


def test_policy_that_is_not_a_mapping_rejects():
    for bad in (None, [], "stages: {}"):
        d = gate("feature_accept", passes("feature_accept"), policy=bad)
        assert d.verdict == "REJECT" and has(d.reasons, "policy:invalid")


def test_load_policy_fails_loudly_on_bad_files(tmp_path):
    (tmp_path / "broken.yaml").write_text("checks: [unclosed\n")
    with pytest.raises(PolicyError, match="invalid YAML"):
        load_policy(tmp_path / "broken.yaml")
    bad = copy.deepcopy(POLICY)
    bad["stages"]["model_scale"]["approvals"][1]["quorum"] = 2
    (tmp_path / "typo.yaml").write_text(yaml.safe_dump(bad))
    with pytest.raises(PolicyError, match="unknown keys"):
        load_policy(tmp_path / "typo.yaml")
    with pytest.raises(FileNotFoundError):
        load_policy(tmp_path / "missing.yaml")


def test_pipeline_merge_requires_oracle_independence_from_ci():
    req = POLICY["stages"]["pipeline_merge"]["requires"]
    assert req == ["pipeline.ci_build", "pipeline.regression_diff", "pipeline.oracle_independence"]
    assert all(POLICY["checks"][c]["allowed_producers"] == ["ci:pipeline"] for c in req)
    # a green build + identical regression diff cannot carry a change that calibrates to the oracle
    calibrated = [ev("pipeline.ci_build"), ev("pipeline.regression_diff"),
                  ev("pipeline.oracle_independence", "FAIL")]
    d = gate("pipeline_merge", calibrated)
    assert d.verdict == "REJECT" and has(d.reasons, "pipeline.oracle_independence")
    assert gate("pipeline_merge", calibrated[:2]).verdict == "HOLD"           # missing => HOLD, never ALLOW
    assert gate("pipeline_merge", passes("pipeline_merge")).verdict == "ALLOW"


def test_unknown_stage_rejects():
    d = gate("model_yolo", passes("model_shadow"))
    assert d.verdict == "REJECT"
    assert has(d.reasons, "policy:unknown_stage")
    assert d.policy_digest == digest(POLICY) and d.subject_digest == digest(SUBJECT)


@pytest.mark.parametrize("kwargs, reason", [
    ({"subject": {}}, "request:invalid_subject"),
    ({"subject": {"x": float("nan")}}, "request:invalid_subject"),
    ({"requested_by": ""}, "request:missing_requested_by"),
    ({"now": "next tuesday"}, "request:invalid_now"),
])
def test_malformed_request_rejects(kwargs, reason):
    d = gate("feature_accept", passes("feature_accept"), **kwargs)
    assert d.verdict == "REJECT" and has(d.reasons, reason)


# ----------------------------------------------------------------------------- evidence semantics

def test_all_required_pass_allows():
    recs = passes("model_shadow")
    d = gate("model_shadow", recs)
    assert d.verdict == "ALLOW" and d.reasons == ()
    assert set(d.evidence_ids) == {r.evidence_id for r in recs} and d.ignored == ()


def test_missing_check_holds_and_says_what_unblocks():
    d = gate("model_shadow", [ev("model.sealed_eval")])
    assert d.verdict == "HOLD"
    [reason] = d.reasons
    assert reason.startswith("model.leakage:missing") and "verifier:leakage" in reason


def test_retried_pass_after_error_allows():
    err = ev("feature.leakage", "ERROR", at=-120)
    ok = ev("feature.leakage", "PASS", at=-60)
    d = gate("feature_accept", [err, ok])
    assert d.verdict == "ALLOW" and d.evidence_ids == (ok.evidence_id,)
    assert f"{err.evidence_id}:older_than:{ok.evidence_id}" in d.ignored


@pytest.mark.parametrize("outcome", ["ERROR", "INCONCLUSIVE"])
def test_latest_error_or_inconclusive_holds(outcome):
    d = gate("feature_accept", [ev("feature.leakage", "PASS", at=-120), ev("feature.leakage", outcome)])
    assert d.verdict == "HOLD"
    assert d.reasons[0].startswith(f"feature.leakage:{outcome.lower()}") and "unblock" in d.reasons[0]


def test_authenticated_fail_rejects():
    fail = ev("model.sealed_eval", "FAIL", limitations=["auc 0.52 below 0.60"])
    d = gate("model_shadow", [fail, ev("model.leakage")])
    assert d.verdict == "REJECT"
    assert d.reasons[0].startswith("model.sealed_eval:fail") and "auc 0.52" in d.reasons[0]
    assert fail.evidence_id in d.evidence_ids


def test_two_honest_passes_allow():
    # Draft bug: two PASS records for one check were "ambiguous" and REJECTed.
    a, b = ev("feature.leakage", at=-90), ev("feature.leakage", at=-30)
    d = gate("feature_accept", [a, b])
    assert d.verdict == "ALLOW" and d.evidence_ids == (b.evidence_id,)


def test_duplicate_delivery_is_harmless():
    a = ev("feature.leakage")
    d = gate("feature_accept", [a, a])
    assert d.verdict == "ALLOW" and f"{a.evidence_id}:duplicate_delivery" in d.ignored


def test_same_timestamp_ties_break_by_input_order():
    err, ok = ev("feature.leakage", "ERROR", at=-10), ev("feature.leakage", "PASS", at=-10)
    assert gate("feature_accept", [err, ok]).verdict == "ALLOW"
    assert gate("feature_accept", [ok, err]).verdict == "HOLD"


def test_later_pass_does_not_quietly_overturn_a_fail():
    fail = ev("feature.leakage", "FAIL", at=-120)
    retry = ev("feature.leakage", "PASS", at=-60)
    d = gate("feature_accept", [fail, retry])
    assert d.verdict == "REJECT"
    assert f"{retry.evidence_id}:outranked_by_standing_fail:{fail.evidence_id}" in d.ignored


def test_explicit_supersede_overturns_a_fail():
    fail = ev("feature.leakage", "FAIL", at=-120)
    fix = ev("feature.leakage", "PASS", at=-60, version="2", supersedes=fail.evidence_id,
             limitations=["v1 compared against the wrong build"])
    d = gate("feature_accept", [fail, fix])
    assert d.verdict == "ALLOW" and d.evidence_ids == (fix.evidence_id,)
    assert f"{fail.evidence_id}:superseded_by:{fix.evidence_id}" in d.ignored


def test_explicit_supersede_chain_beats_timestamps_and_can_revoke_a_pass():
    ok = ev("feature.leakage", "PASS", at=-10)
    # revocation issued (by clock) before the PASS it revokes: the explicit chain is honoured
    revoke = ev("feature.leakage", "INCONCLUSIVE", at=-20, supersedes=ok.evidence_id)
    d = gate("feature_accept", [ok, revoke])
    assert d.verdict == "HOLD" and d.evidence_ids == (revoke.evidence_id,)
    # chain a <- b <- c: only c stands
    a = ev("feature.leakage", "FAIL", at=-30)
    b = ev("feature.leakage", "ERROR", at=-20, supersedes=a.evidence_id)
    c = ev("feature.leakage", "PASS", at=-10, supersedes=b.evidence_id)
    assert gate("feature_accept", [a, b, c]).verdict == "ALLOW"


def test_untrusted_producer_can_neither_reject_nor_allow():
    ok = ev("feature.leakage")
    noise = ev("feature.leakage", "FAIL", at=-1, producer="agent:ds-2")
    d = gate("feature_accept", [ok, noise])
    assert d.verdict == "ALLOW"
    assert f"{noise.evidence_id}:unauthorised_producer:agent:ds-2" in d.ignored
    self_pass = ev("feature.leakage", "PASS", producer="agent:ds-2")
    d2 = gate("feature_accept", [self_pass])
    assert d2.verdict == "HOLD" and has(d2.reasons, "feature.leakage:missing")


def test_unauthorised_record_cannot_supersede_an_authorised_fail():
    fail = ev("feature.leakage", "FAIL", at=-60)
    cover = ev("feature.leakage", "PASS", at=-1, producer="agent:ds-1", supersedes=fail.evidence_id)
    assert gate("feature_accept", [fail, cover]).verdict == "REJECT"


def test_evidence_produced_by_the_requester_is_not_independent():
    rec = ev("feature.leakage")
    d = gate("feature_accept", [rec], requested_by="verifier:leakage")
    assert d.verdict == "HOLD" and f"{rec.evidence_id}:produced_by_requester" in d.ignored


def test_wrong_subject_evidence_is_ignored_both_ways():
    other_pass = ev("feature.leakage", subject=OTHER)
    d = gate("feature_accept", [other_pass])
    assert d.verdict == "HOLD" and f"{other_pass.evidence_id}:wrong_subject" in d.ignored
    other_fail = ev("feature.leakage", "FAIL", subject=OTHER, at=-1)
    assert gate("feature_accept", [ev("feature.leakage"), other_fail]).verdict == "ALLOW"


def test_check_not_required_for_stage_is_ignored():
    unrelated = ev("answer.reconcile", "FAIL")
    d = gate("feature_accept", [ev("feature.leakage"), unrelated])
    assert d.verdict == "ALLOW" and has(d.ignored, f"{unrelated.evidence_id}:not_required_for_stage")


def test_stale_pass_holds_by_max_age_and_by_expiry():
    old = ev("feature.leakage", at=-60 * 24 * 31)       # feature.leakage uses the 30-day default
    d = gate("feature_accept", [old])
    assert d.verdict == "HOLD" and has(d.reasons, "feature.leakage:stale") and "max_age_s" in d.reasons[0]
    expired = ev("feature.leakage", at=-60, expires_in_min=30)
    d2 = gate("feature_accept", [expired])
    assert d2.verdict == "HOLD" and "expired" in d2.reasons[0]
    fresh_ttl = ev("feature.leakage", at=-60, expires_in_min=120)
    assert gate("feature_accept", [fresh_ttl]).verdict == "ALLOW"


def test_old_fail_still_rejects():
    assert gate("feature_accept", [ev("feature.leakage", "FAIL", at=-60 * 24 * 90)]).verdict == "REJECT"


def test_edited_record_is_unverifiable_and_holds_instead_of_allowing():
    fail = ev("feature.leakage", "FAIL")
    edited = replace(fail, outcome="PASS")              # someone edited the JSONL line
    d = gate("feature_accept", [edited])
    assert d.verdict == "HOLD"
    assert d.reasons[0].startswith("feature.leakage:unverifiable") and "edited or corrupt" in d.reasons[0]
    # ...and a later fresh PASS does not paper over it until the bad record is explicitly superseded
    assert gate("feature_accept", [edited, ev("feature.leakage", at=-1)]).verdict == "HOLD"
    fix = ev("feature.leakage", at=-1, supersedes=edited.evidence_id)
    assert gate("feature_accept", [edited, fix]).verdict == "ALLOW"


@pytest.mark.parametrize("changes, problem", [
    ({"outcome": "pass"}, "outcome"),
    ({"outcome": True}, "outcome"),
    ({"issued_at": "yesterday-ish"}, "unparseable"),
    ({"subject": OTHER}, "subject does not hash"),
    ({"limitations": "none"}, "limitations"),
])
def test_garbled_authorised_record_holds_never_rejects(changes, problem):
    garbled = forged(ev("feature.leakage", "FAIL"), **changes)
    d = gate("feature_accept", [ev("feature.leakage", at=-90), garbled])
    assert d.verdict == "HOLD"
    assert has(d.reasons, "feature.leakage:unverifiable") and problem in d.reasons[0]


def test_future_dated_record_is_unverifiable_beyond_clock_skew():
    future = ev("feature.leakage", at=+60)
    d = gate("feature_accept", [future])
    assert d.verdict == "HOLD" and "in the future" in d.reasons[0]
    within_skew = ev("feature.leakage", at=+0.5)      # 30 s ahead, policy skew is 60 s
    assert gate("feature_accept", [within_skew]).verdict == "ALLOW"


def test_check_version_pinning():
    pinned = copy.deepcopy(POLICY)
    pinned["checks"]["feature.leakage"]["versions"] = ["2"]
    v1_pass, v2_pass = ev("feature.leakage", version="1"), ev("feature.leakage", version="2")
    d = gate("feature_accept", [v1_pass], policy=pinned)
    assert d.verdict == "HOLD" and f"{v1_pass.evidence_id}:check_version_not_accepted:1" in d.ignored
    assert gate("feature_accept", [v1_pass, v2_pass], policy=pinned).verdict == "ALLOW"
    v1_fail = ev("feature.leakage", "FAIL", version="1", at=-1)
    assert gate("feature_accept", [v2_pass, v1_fail], policy=pinned).verdict == "ALLOW"


def test_non_records_in_input_are_ignored_not_fatal():
    d = gate("feature_accept", [{"outcome": "FAIL"}, ev("feature.leakage")])
    assert d.verdict == "ALLOW" and "#0:not_an_evidence_record" in d.ignored


# ----------------------------------------------------------------------------- approvals

def test_self_approval_refused():
    recs = passes("model_experiment")
    d = gate("model_experiment", recs, [appr("model_experiment", REQ, "business_owner")])
    assert d.verdict == "HOLD"
    assert has(d.reasons, "approval:business_owner:missing") and "refused self-approvals" in d.reasons[0]
    assert has(d.ignored, "ap-") and any(e.endswith(":self_approval_refused") for e in d.ignored)
    # the approver asked for it themselves on someone's behalf: still self-approval
    proxy = appr("model_experiment", "user:li", "business_owner", requested_by="user:li")
    assert gate("model_experiment", recs, [proxy]).verdict == "HOLD"
    ok = appr("model_experiment", "user:li", "business_owner")
    d2 = gate("model_experiment", recs, [ok])
    assert d2.verdict == "ALLOW" and d2.approval_ids == (ok.approval_id,)


def test_self_approval_allowed_only_when_policy_says_so():
    lenient = copy.deepcopy(POLICY)
    lenient["stages"]["model_experiment"]["approvals"][0]["no_self_approval"] = False
    a = appr("model_experiment", REQ, "business_owner")
    assert gate("model_experiment", passes("model_experiment"), [a], policy=lenient).verdict == "ALLOW"


def test_model_scale_needs_readout_and_two_distinct_people():
    base = passes("model_shadow")
    bo, risk = appr("model_scale", "user:li", "business_owner"), appr("model_scale", "user:zhao", "risk")
    d = gate("model_scale", base, [bo, risk])
    assert d.verdict == "HOLD" and has(d.reasons, "experiment.readout:missing")
    full = passes("model_scale")
    assert gate("model_scale", full, [bo, risk]).verdict == "ALLOW"
    inconclusive = [*full, ev("experiment.readout", "INCONCLUSIVE", at=-1)]
    assert gate("model_scale", inconclusive, [bo, risk]).verdict == "HOLD"
    # one person wearing both hats is one approval, not two
    li_risk = appr("model_scale", "user:li", "risk")
    d2 = gate("model_scale", full, [bo, li_risk])
    assert d2.verdict == "HOLD" and has(d2.reasons, "approval:")
    # li (both roles) + chen (business_owner): matching puts li on risk, chen on business_owner
    chen = appr("model_scale", "user:chen", "business_owner")
    d3 = gate("model_scale", full, [bo, li_risk, chen])
    assert d3.verdict == "ALLOW" and len(d3.approval_ids) == 2


def test_approvals_bind_to_stage_and_subject():
    recs = passes("model_experiment")
    wrong_stage = appr("model_shadow", "user:li", "business_owner")
    wrong_subject = appr("model_experiment", "user:li", "business_owner", subject=OTHER)
    wrong_role = appr("model_experiment", "user:li", "intern")
    d = gate("model_experiment", recs, [wrong_stage, wrong_subject, wrong_role])
    assert d.verdict == "HOLD" and d.approval_ids == ()
    assert has(d.ignored, f"{wrong_stage.approval_id}:other_stage")
    assert has(d.ignored, f"{wrong_subject.approval_id}:wrong_subject")
    assert has(d.ignored, f"{wrong_role.approval_id}:role_not_required")
    edited = replace(appr("model_experiment", "user:li", "business_owner"), approver="user:boss")
    d2 = gate("model_experiment", recs, [edited])
    assert d2.verdict == "HOLD" and has(d2.ignored, f"{edited.approval_id}:unverifiable")


def test_role_membership_enforced_when_configured():
    strict = copy.deepcopy(POLICY)
    strict["roles"] = {"business_owner": {"members": ["user:li"]}, "risk": {"members": ["user:zhao"]}}
    assert policy_problems(strict) == []
    recs = passes("model_experiment")
    outsider = appr("model_experiment", "user:mallory", "business_owner")
    d = gate("model_experiment", recs, [outsider], policy=strict)
    assert d.verdict == "HOLD" and has(d.ignored, f"{outsider.approval_id}:approver_not_member_of")
    member = appr("model_experiment", "user:li", "business_owner")
    assert gate("model_experiment", recs, [member], policy=strict).verdict == "ALLOW"


# ----------------------------------------------------------------------------- properties

_CHECKS = ["model.sealed_eval", "model.leakage", "answer.reconcile"]
_record = st.tuples(st.sampled_from(_CHECKS), st.sampled_from(OUTCOMES), st.booleans(), st.booleans(),
                    st.integers(min_value=-600, max_value=0))


def _soup(spec):
    out = []
    for check, outcome, authorised, right_subject, minute in spec:
        out.append(ev(check, outcome, at=minute, producer=None if authorised else "agent:rogue",
                      subject=SUBJECT if right_subject else OTHER))
    return out


@settings(max_examples=150, deadline=None, database=None)   # no .hypothesis/ dir in the repo
@given(base=st.lists(_record, max_size=8), noise=st.lists(_record, max_size=5))
def test_property_reject_only_on_authenticated_fail_and_noise_is_inert(base, noise):
    records = _soup(base)
    d = gate("model_shadow", records)
    counted = [r for r in records if r.subject_digest == digest(SUBJECT) and r.producer != "agent:rogue"
               and r.check_id in POLICY["stages"]["model_shadow"]["requires"]]
    # REJECT <=> an authenticated FAIL exists (no supersedes in these soups)
    assert (d.verdict == "REJECT") == any(r.outcome == "FAIL" for r in counted)
    # ALLOW => every required check's latest counted record is a PASS
    if d.verdict == "ALLOW":
        for check in POLICY["stages"]["model_shadow"]["requires"]:
            mine = [(r.issued_at, i, r) for i, r in enumerate(counted) if r.check_id == check]
            assert mine and max(mine, key=lambda t: t[:2])[2].outcome == "PASS"   # ties: later input wins
    # every input record is accounted for exactly once
    assert len(d.evidence_ids) + len(d.ignored) == len(records)
    # unauthorised / wrong-subject / unrequired records never change the verdict
    inert = _soup([(c, o, False, s, m) for c, o, _, s, m in noise])
    inert += _soup([(c, o, a, False, m) for c, o, a, _, m in noise])
    inert += _soup([("answer.reconcile", o, a, s, m) for _, o, a, s, m in noise])
    assert gate("model_shadow", records + inert).verdict == d.verdict


# ----------------------------------------------------------------------------- stores

@pytest.fixture()
def ws(tmp_path) -> Workspace:
    return Workspace(tmp_path / "ws").ensure()


def test_store_round_trip_is_append_only_and_idempotent(ws, monkeypatch):
    monkeypatch.setenv("DSH_NOW", NOW)
    store = EvidenceStore(ws)
    a = store.record(check_id="feature.leakage", check_version="1", stage="feature_accept", subject=SUBJECT,
                     outcome="ERROR", metrics={"reason": "build lock timeout"}, producer="verifier:leakage")
    first_line = store.path.read_text().splitlines()[0]
    monkeypatch.setenv("DSH_NOW", ts(5))
    b = store.record(check_id="feature.leakage", check_version="1", stage="feature_accept", subject=SUBJECT,
                     outcome="PASS", metrics={"n_compared": 812}, producer="verifier:leakage", ttl_s=3600,
                     limitations=["PIT window 2026-03..04 only"])
    store.record(check_id="feature.leakage", check_version="1", stage="feature_accept", subject=OTHER,
                 outcome="FAIL", metrics={}, producer="verifier:leakage")
    lines = store.path.read_text().splitlines()
    assert len(lines) == 3 and lines[0] == first_line
    assert store.all()[:2] == [a, b]
    assert store.for_subject(digest(SUBJECT)) == [a, b]
    assert b.expires_at == ts(65) and b.issued_at == ts(5) and b.subject_digest == digest(SUBJECT)
    again = store.record(check_id="feature.leakage", check_version="1", stage="feature_accept",
                         subject=SUBJECT, outcome="PASS", metrics={"n_compared": 812},
                         producer="verifier:leakage", ttl_s=3600, limitations=["PIT window 2026-03..04 only"])
    assert again == b and len(store.path.read_text().splitlines()) == 3
    d = gate("feature_accept", store.for_subject(digest(SUBJECT)), now=ts(10))
    assert d.verdict == "ALLOW" and d.evidence_ids == (b.evidence_id,)
    assert gate("feature_accept", store.for_subject(digest(SUBJECT)), now=ts(70)).verdict == "HOLD"


def test_store_refuses_ambiguous_or_lossy_input(ws, monkeypatch):
    monkeypatch.setenv("DSH_NOW", NOW)
    store = EvidenceStore(ws)
    kw = dict(check_id="model.sealed_eval", check_version="1", stage="model_shadow", subject=SUBJECT,
              metrics={}, producer="evaluator:custody")
    for bad_outcome in (True, "pass", "OK", None):
        with pytest.raises(ValueError, match="outcome"):
            store.record(**{**kw, "outcome": bad_outcome})
    with pytest.raises(ValueError, match="finite"):
        store.record(**{**kw, "outcome": "PASS", "metrics": {"auc": float("nan")}})
    with pytest.raises(ValueError, match="ttl_s"):
        store.record(**kw, outcome="PASS", ttl_s=0)
    with pytest.raises(ValueError, match="subject"):
        store.record(**{**kw, "outcome": "PASS", "subject": {}})
    with pytest.raises(ValueError, match="no such evidence"):
        store.record(**kw, outcome="PASS", supersedes="ev-000000000000000000000000")
    other = store.record(**{**kw, "check_id": "model.leakage", "producer": "verifier:leakage"}, outcome="FAIL")
    with pytest.raises(ValueError, match="different subject or check"):
        store.record(**kw, outcome="PASS", supersedes=other.evidence_id)
    with pytest.raises(TypeError, match="not JSON-serialisable"):
        store.record(**{**kw, "outcome": "PASS", "metrics": {"model": object()}})
    assert len(store.all()) == 1      # only the valid FAIL was written


def test_store_normalises_numpy_metrics_without_breaking_integrity(ws, monkeypatch):
    monkeypatch.setenv("DSH_NOW", NOW)
    rec = EvidenceStore(ws).record(
        check_id="model.sealed_eval", check_version="1", stage="model_shadow", subject=SUBJECT,
        outcome="PASS", metrics={"n": np.int64(812), "auc": np.float64(0.71), "ci": np.array([0.68, 0.74])},
        producer="evaluator:custody")
    assert rec.metrics == {"n": 812, "auc": 0.71, "ci": [0.68, 0.74]}
    [loaded] = EvidenceStore(ws).all()
    assert loaded == rec and loaded.content_id() == loaded.evidence_id


def test_corrupt_store_line_fails_loudly(ws, monkeypatch):
    monkeypatch.setenv("DSH_NOW", NOW)
    store = EvidenceStore(ws)
    store.record(check_id="feature.leakage", check_version="1", stage="feature_accept", subject=SUBJECT,
                 outcome="PASS", metrics={}, producer="verifier:leakage")
    with open(store.path, "a") as f:
        f.write('{"evidence_id": "ev-x", "outcome": "PA\n')
    with pytest.raises(EvidenceStoreError, match=r"evidence\.jsonl:2: not valid JSON"):
        store.all()
    store.path.write_text(json.dumps({"evidence_id": "ev-x", "outcome": "PASS"}) + "\n")
    with pytest.raises(EvidenceStoreError, match="expected fields"):
        store.for_subject(digest(SUBJECT))


def test_approval_store_round_trip(ws, monkeypatch):
    monkeypatch.setenv("DSH_NOW", NOW)
    store = ApprovalStore(ws)
    with pytest.raises(ValueError, match="exactly one"):
        store.record(stage="model_experiment", approver="user:li", role="business_owner", requested_by=REQ)
    with pytest.raises(ValueError, match="exactly one"):
        store.record(stage="model_experiment", approver="user:li", role="business_owner", requested_by=REQ,
                     subject=SUBJECT, subject_digest=digest(SUBJECT))
    a = store.record(stage="model_experiment", approver="user:li", role="business_owner", requested_by=REQ,
                     subject=SUBJECT)
    b = store.record(stage="model_experiment", approver="user:zhao", role="risk", requested_by=REQ,
                     subject_digest=digest(OTHER))
    assert store.all() == [a, b] and store.for_subject(digest(SUBJECT)) == [a]
    assert a.approval_id == a.content_id() and a.issued_at == NOW


def test_decide_end_to_end_logs_every_decision(ws, monkeypatch):
    monkeypatch.setenv("DSH_NOW", NOW)
    evs = EvidenceStore(ws)
    for check in POLICY["stages"]["model_experiment"]["requires"]:
        evs.record(check_id=check, check_version="1", stage="model_shadow", subject=SUBJECT, outcome="PASS",
                   metrics={}, producer=PRODUCER[check])
    evs.record(check_id="model.sealed_eval", check_version="1", stage="model_shadow", subject=OTHER,
               outcome="FAIL", metrics={}, producer="evaluator:custody")
    d1 = decide(ws, "model_experiment", SUBJECT, REQ)
    assert d1.verdict == "HOLD" and d1.ignored == ()          # the OTHER subject's FAIL is not even loaded
    ApprovalStore(ws).record(stage="model_experiment", approver=REQ, role="business_owner",
                             requested_by=REQ, subject=SUBJECT)
    assert decide(ws, "model_experiment", SUBJECT, REQ).verdict == "HOLD"
    ApprovalStore(ws).record(stage="model_experiment", approver="user:li", role="business_owner",
                             requested_by=REQ, subject=SUBJECT)
    d3 = decide(ws, "model_experiment", SUBJECT, REQ)
    assert d3.verdict == "ALLOW" and len(d3.approval_ids) == 1
    assert decide(ws, "model_scale", SUBJECT, REQ).verdict == "HOLD"
    d5 = decide(ws, "model_experiment", SUBJECT, REQ, policy_path=ws.root / "no_such_policy.yaml")
    assert d5.verdict == "REJECT" and has(d5.reasons, "policy:load_error")
    log = [json.loads(x) for x in (ws.registry / "gate_decisions.jsonl").read_text().splitlines()]
    assert [x["verdict"] for x in log] == ["HOLD", "HOLD", "ALLOW", "HOLD", "REJECT"]
    assert all(x["decided_at"] == NOW and x["requested_by"] == REQ for x in log)
    assert log[2]["policy_digest"] == digest(POLICY) and isinstance(log[2]["evidence_ids"], list)


# ----------------------------------------------------------------------------- targeting policies

UPLIFT_POLICY = {"kind": "policy", "policy": "uplift_value_top_k", "k": 500, "channel": "app",
                 "as_of": "2026-07-01", "score_build": "2026-06-30_abcd1234",
                 "models": {"uplift_model": {"model_id": "u" * 16, "artifact_digest": "a" * 64}}}
RISK_POLICY = {**UPLIFT_POLICY, "policy": "risk_top_k",
               "models": {"churn_candidate": {"candidate_id": "c0ffee", "artifact_digest": "b" * 64}}}


def _policy_base(subject: dict) -> list[EvidenceRecord]:
    return [ev("policy.models", subject=subject), ev("policy.offline_qini", subject=subject)]


def test_policy_stages_require_policy_evidence_not_model_evidence():
    assert POLICY["checks"]["policy.models"]["allowed_producers"] == ["verifier:policy"]
    assert POLICY["checks"]["policy.offline_qini"]["allowed_producers"] == ["verifier:uplift"]
    assert set(POLICY["stages"]["model_experiment"]["requires"]) == {"policy.models", "policy.offline_qini"}
    assert set(POLICY["stages"]["model_scale"]["requires"]) == {"policy.models", "policy.offline_qini",
                                                                "experiment.readout"}
    bo = appr("model_experiment", "user:li", "business_owner", subject=UPLIFT_POLICY)
    # a model's own evidence (even on the policy subject) never clears a policy stage
    model_only = [ev(c, subject=UPLIFT_POLICY) for c in ("model.sealed_eval", "model.leakage")]
    d = gate("model_experiment", model_only, [bo], subject=UPLIFT_POLICY)
    assert d.verdict == "HOLD" and has(d.reasons, "policy.models:missing") and \
        has(d.reasons, "policy.offline_qini:missing")
    assert sum(":not_required_for_stage:" in i for i in d.ignored) == 2
    assert gate("model_experiment", _policy_base(UPLIFT_POLICY), [bo], subject=UPLIFT_POLICY).verdict == "ALLOW"
    # nor does a candidate subject: a model is shadowed, a policy is experimented on
    assert gate("model_experiment", passes("model_shadow"), [appr("model_experiment", "user:li",
                                                                  "business_owner")]).verdict == "HOLD"


def test_failed_offline_validation_rejects_the_experiment_whatever_the_approvals():
    fail = ev("policy.offline_qini", "FAIL", subject=UPLIFT_POLICY,
              limitations=["held-out operating point lost money"])
    recs = [ev("policy.models", subject=UPLIFT_POLICY), fail]
    bo = appr("model_experiment", "user:li", "business_owner", subject=UPLIFT_POLICY)
    d = gate("model_experiment", recs, [bo], subject=UPLIFT_POLICY)
    assert d.verdict == "REJECT" and has(d.reasons, "policy.offline_qini:fail")
    # the policy author's own "validation", or a verifier that does not own this check, is not counted
    own = ev("policy.offline_qini", producer=REQ, subject=UPLIFT_POLICY, at=-1)
    other = ev("policy.offline_qini", producer="verifier:leakage", subject=UPLIFT_POLICY, at=-1)
    d2 = gate("model_experiment", [ev("policy.models", subject=UPLIFT_POLICY), own, other], [bo],
              subject=UPLIFT_POLICY)
    assert d2.verdict == "HOLD" and has(d2.reasons, "policy.offline_qini:missing")
    assert has(d2.ignored, f"{own.evidence_id}:unauthorised_producer")
    # a later PASS does not quietly overturn the FAIL; scaling is rejected by the same evidence
    retry = ev("policy.offline_qini", "PASS", subject=UPLIFT_POLICY, at=-1)
    assert gate("model_experiment", [*recs, retry], [bo], subject=UPLIFT_POLICY).verdict == "REJECT"
    readout = ev("experiment.readout", "PASS", subject=UPLIFT_POLICY)
    scale = [appr("model_scale", "user:li", "business_owner", subject=UPLIFT_POLICY),
             appr("model_scale", "user:zhao", "risk", subject=UPLIFT_POLICY)]
    assert gate("model_scale", [*recs, readout], scale, subject=UPLIFT_POLICY).verdict == "REJECT"


def test_each_policy_scales_on_its_own_readout():
    def scale_approvals(subject):
        return [appr("model_scale", "user:li", "business_owner", subject=subject),
                appr("model_scale", "user:zhao", "risk", subject=subject)]
    uplift_pass = ev("experiment.readout", "PASS", subject=UPLIFT_POLICY)
    risk_inconclusive = ev("experiment.readout", "INCONCLUSIVE", subject=RISK_POLICY)
    every = [*_policy_base(UPLIFT_POLICY), *_policy_base(RISK_POLICY), uplift_pass, risk_inconclusive]
    up = gate("model_scale", every, scale_approvals(UPLIFT_POLICY), subject=UPLIFT_POLICY)
    assert up.verdict == "ALLOW" and uplift_pass.evidence_id in up.evidence_ids
    risk = gate("model_scale", every, scale_approvals(RISK_POLICY), subject=RISK_POLICY)
    assert risk.verdict == "HOLD" and has(risk.reasons, "experiment.readout:inconclusive")
    assert f"{uplift_pass.evidence_id}:wrong_subject" in risk.ignored      # another policy's PASS never transfers
    risk_fail = ev("experiment.readout", "FAIL", subject=RISK_POLICY, at=-1)
    assert gate("model_scale", [*every, risk_fail], scale_approvals(RISK_POLICY),
                subject=RISK_POLICY).verdict == "REJECT"
    # the approvals must be distinct people other than the requester, per policy
    self_scale = [appr("model_scale", REQ, "business_owner", subject=UPLIFT_POLICY),
                  appr("model_scale", "user:zhao", "risk", subject=UPLIFT_POLICY)]
    assert gate("model_scale", every, self_scale, subject=UPLIFT_POLICY).verdict == "HOLD"
    assert gate("model_scale", every, scale_approvals(RISK_POLICY), subject=UPLIFT_POLICY).verdict == "HOLD"


# ----------------------------------------------------------------------------- CI example

def test_ci_example_is_manual_only_and_gates_before_the_protected_environment():
    wf = yaml.safe_load((ROOT / ".github/workflows/dsh-gate.example.yml").read_text())
    triggers = wf.get("on", wf.get(True))         # YAML 1.1 reads a bare `on` key as True
    assert set(triggers) == {"workflow_dispatch"}  # example: never runs on push/PR by accident
    jobs = wf["jobs"]
    text = json.dumps(wf)
    assert "dsh gate" in text and "actions/attest" in text and "gh attestation verify" in text
    deploy = [j for j in jobs.values() if "environment" in j]
    assert deploy and all(j.get("needs") for j in deploy)
