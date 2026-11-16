"""Promotion gate: required evidence + approvals -> ALLOW | HOLD | REJECT.

Why this exists: an agent saying "done" is not permission to ship. A stage is allowed only
when policy-required checks have a current PASS from an authorised producer and the
required people have signed off -- and the gate must never turn missing, crashed or
unverifiable evidence into success, nor let noise turn into a terminal REJECT.

TRUST BOUNDARY
  * The policy (``policies/gates.yaml``) is trusted, code-reviewed configuration; its digest
    is stamped on every decision. A policy, stage or request the gate cannot interpret is a
    configuration error and yields REJECT: configuration error is not permission.
  * Evidence and approvals arrive over untrusted transport (registry files anyone with write
    access can append to). Each record is authorised and integrity-checked BEFORE it is counted
    (the draft gate counted first and could be forced to REJECT by unsigned input):
      - another subject, a check the stage does not require, a producer the check does not
        allow, or a record produced by the requester itself -> ignored; it cannot move the
        verdict in either direction;
      - claims an allowed producer but is malformed, edited (content id mismatch) or
        future-dated -> unverifiable -> HOLD for that check, until an authorised producer
        re-issues a valid record that supersedes it (so corrupting a FAIL cannot hide it).
    REJECT therefore requires an authenticated FAIL (or a configuration error).
  * Per required check (supersede/retry semantics, which the draft lacked):
      - a valid authorised record's ``supersedes`` removes its target from consideration;
      - a FAIL stands until it is explicitly superseded. A later PASS for the same subject and
        check does not quietly overturn it: retry-until-green is how failures become successes.
        A fixed candidate is a new subject; an evaluator bug is fixed by a superseding record;
      - otherwise the latest record wins (``issued_at``; ties -> later in the input), so a
        retried PASS after ERROR/INCONCLUSIVE counts and duplicate honest PASSes are harmless;
      - the winner must be a PASS that is neither expired nor older than ``max_age_s``.
  * Approvals: bound to stage + subject; self-approval (approver is the requester, or the
    requester recorded on the approval) is refused; each person counts once per decision even
    across roles (separation of duties), via a small bipartite matching.
  * Producer and approver strings are identity *claims* here. Production binds them to CI OIDC
    identities with Sigstore/in-toto attestations and protected-environment reviewers; the
    counting logic above is unchanged by that.

A record's ``stage`` field is informational (where it was produced): evidence binds to the
subject + check, so a sealed evaluation recorded for ``model_shadow`` also serves ``model_scale``.

Every input record ends up in exactly one of ``evidence_ids`` (it determined the verdict) or
``ignored`` (with the reason), so a decision can be audited without re-running it.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from ..common import append_jsonl, digest, parse_ts, utcnow
from ..workspace import Workspace
from .records import OUTCOMES, Approval, ApprovalStore, EvidenceRecord, EvidenceStore, iso_utc

VERDICTS = ("ALLOW", "HOLD", "REJECT")
DEFAULT_POLICY_PATH = Path(__file__).resolve().parent.parent / "policies" / "gates.yaml"
DECISION_LOG = "gate_decisions.jsonl"
DEFAULT_CLOCK_SKEW_S = 60

_TOP_KEYS = {"version", "policy_id", "description", "defaults", "roles", "checks", "stages"}
_DEFAULT_KEYS = {"max_age_s", "clock_skew_s"}
_ROLE_KEYS = {"description", "members"}
_CHECK_KEYS = {"description", "allowed_producers", "max_age_s", "versions"}
_STAGE_KEYS = {"description", "requires", "approvals"}
_APPROVAL_KEYS = {"role", "count", "no_self_approval"}


class PolicyError(ValueError):
    """The policy file cannot be parsed or does not satisfy the policy schema."""


@dataclass(frozen=True)
class GateDecision:
    verdict: str                    # ALLOW | HOLD | REJECT
    stage: str
    subject_digest: str
    policy_digest: str
    reasons: tuple[str, ...]        # blocking reasons, REJECT causes first; empty on ALLOW
    evidence_ids: tuple[str, ...]   # records that determined the verdict
    ignored: tuple[str, ...]        # "<id>:<why>" for every other input record / approval
    approval_ids: tuple[str, ...] = ()   # approvals counted toward the quorum

    def to_dict(self) -> dict:
        d = asdict(self)
        return {k: list(v) if isinstance(v, tuple) else v for k, v in d.items()}


# --------------------------------------------------------------------------- policy

def _is_str(x) -> bool:
    return isinstance(x, str) and bool(x.strip())


def _is_pos_int(x) -> bool:
    return type(x) is int and x > 0


def _is_str_list(x) -> bool:
    return isinstance(x, list) and bool(x) and all(_is_str(i) for i in x) and len(set(x)) == len(x)


def _unknown(where: str, d: dict, allowed: set[str]) -> list[str]:
    extra = sorted(map(str, set(d) - allowed))
    return [f"{where}: unknown keys {extra}"] if extra else []


def policy_problems(policy) -> list[str]:
    """Every way ``policy`` violates the schema; empty list == usable. Unknown keys are errors
    (a misspelt ``no_self_aproval`` must not silently fall back to a default)."""
    if not isinstance(policy, dict):
        return [f"policy is a {type(policy).__name__}, not a mapping"]
    p = _unknown("policy", policy, _TOP_KEYS)
    if type(policy.get("version")) is not int or policy.get("version") != 1:
        p.append(f"version must be 1, got {policy.get('version')!r}")

    defaults = policy.get("defaults", {})
    if not isinstance(defaults, dict):
        p.append("defaults must be a mapping")
        defaults = {}
    p += _unknown("defaults", defaults, _DEFAULT_KEYS)
    if "max_age_s" in defaults and not _is_pos_int(defaults["max_age_s"]):
        p.append("defaults.max_age_s must be a positive int")
    skew = defaults.get("clock_skew_s", DEFAULT_CLOCK_SKEW_S)
    if type(skew) is not int or skew < 0:
        p.append("defaults.clock_skew_s must be a non-negative int")

    roles = policy.get("roles")
    if roles is not None:
        if not isinstance(roles, dict) or not roles:
            p.append("roles must be a non-empty mapping when present")
            roles = {}
        for rname, r in roles.items():
            if not isinstance(r, dict):
                p.append(f"role {rname}: must be a mapping")
                continue
            p += _unknown(f"role {rname}", r, _ROLE_KEYS)
            if not _is_str_list(r.get("members")):
                p.append(f"role {rname}: members must be a non-empty list of distinct strings")

    checks = policy.get("checks")
    if not isinstance(checks, dict) or not checks:
        p.append("checks must be a non-empty mapping")
        checks = {}
    for cid, c in checks.items():
        if not _is_str(cid):
            p.append(f"check id {cid!r} must be a non-empty string")
        if not isinstance(c, dict):
            p.append(f"check {cid}: must be a mapping")
            continue
        p += _unknown(f"check {cid}", c, _CHECK_KEYS)
        if not _is_str_list(c.get("allowed_producers")):
            p.append(f"check {cid}: allowed_producers must be a non-empty list of distinct strings")
        if "max_age_s" in c:
            if not _is_pos_int(c["max_age_s"]):
                p.append(f"check {cid}: max_age_s must be a positive int")
        elif not _is_pos_int(defaults.get("max_age_s")):
            p.append(f"check {cid}: no max_age_s and no valid defaults.max_age_s")
        if "versions" in c and not _is_str_list(c["versions"]):
            p.append(f"check {cid}: versions must be a non-empty list of distinct strings")

    stages = policy.get("stages")
    if not isinstance(stages, dict) or not stages:
        p.append("stages must be a non-empty mapping")
        stages = {}
    for sid, s in stages.items():
        if not isinstance(s, dict):
            p.append(f"stage {sid}: must be a mapping")
            continue
        p += _unknown(f"stage {sid}", s, _STAGE_KEYS)
        req = s.get("requires")
        if not _is_str_list(req):
            p.append(f"stage {sid}: requires must be a non-empty list of distinct check ids")
        else:
            p += [f"stage {sid}: requires unknown check {r!r}" for r in req if r not in checks]
        appr = s.get("approvals", [])
        if not isinstance(appr, list):
            p.append(f"stage {sid}: approvals must be a list")
            continue
        seen: set[str] = set()
        for i, a in enumerate(appr):
            where = f"stage {sid}: approvals[{i}]"
            if not isinstance(a, dict):
                p.append(f"{where} must be a mapping")
                continue
            p += _unknown(where, a, _APPROVAL_KEYS)
            role = a.get("role")
            if not _is_str(role):
                p.append(f"{where}.role must be a non-empty string")
            elif role in seen:
                p.append(f"{where}: duplicate role {role!r}")
            elif roles is not None and role not in roles:
                p.append(f"{where}: role {role!r} is not defined under roles")
            if _is_str(role):
                seen.add(role)
            if not _is_pos_int(a.get("count")):
                p.append(f"{where}.count must be a positive int")
            if "no_self_approval" in a and type(a["no_self_approval"]) is not bool:
                p.append(f"{where}.no_self_approval must be true or false")
    return p


def load_policy(path: str | Path | None = None) -> dict:
    """Load and validate the gate policy (default: the packaged ``policies/gates.yaml``).
    Raises ``PolicyError`` on invalid YAML or schema; a missing file raises ``FileNotFoundError``."""
    path = Path(path) if path is not None else DEFAULT_POLICY_PATH
    try:
        policy = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PolicyError(f"{path}: invalid YAML: {exc}") from exc
    problems = policy_problems(policy)
    if problems:
        raise PolicyError(f"{path}: " + "; ".join(problems))
    return policy


# --------------------------------------------------------------------------- record checks

def _record_problem(rec: EvidenceRecord, now: datetime, skew: timedelta) -> str | None:
    """Why an authorised-looking record cannot be trusted, or None."""
    if not _is_str(rec.evidence_id):
        return "missing evidence_id"
    for name in ("check_version", "stage", "issued_at"):
        if not _is_str(getattr(rec, name)):
            return f"{name} is not a non-empty string"
    if not isinstance(rec.outcome, str) or rec.outcome not in OUTCOMES:
        return f"outcome {rec.outcome!r} is not one of {'|'.join(OUTCOMES)}"
    if not isinstance(rec.metrics, dict):
        return "metrics is not a mapping"
    if not isinstance(rec.limitations, list) or not all(isinstance(x, str) for x in rec.limitations):
        return "limitations is not a list of strings"
    if rec.supersedes is not None and not _is_str(rec.supersedes):
        return "supersedes is not a string"
    try:
        issued = parse_ts(rec.issued_at)
        expires = parse_ts(rec.expires_at) if rec.expires_at is not None else None
    except (TypeError, ValueError):
        return "unparseable issued_at/expires_at"
    if issued > now + skew:
        return f"issued_at {rec.issued_at} is in the future"
    if expires is not None and expires <= issued:
        return "expires_at is not after issued_at"
    try:
        if not isinstance(rec.subject, dict) or digest(rec.subject) != rec.subject_digest:
            return "subject does not hash to subject_digest"
        if rec.content_id() != rec.evidence_id:
            return "content does not match evidence_id (edited or corrupt)"
    except (TypeError, ValueError):
        return "record content is not canonical JSON"
    return None


def _approval_problem(a: Approval, now: datetime, skew: timedelta) -> str | None:
    for name in ("approval_id", "approver", "role", "requested_by", "issued_at"):
        if not _is_str(getattr(a, name)):
            return f"{name} is not a non-empty string"
    try:
        issued = parse_ts(a.issued_at)
    except (TypeError, ValueError):
        return "unparseable issued_at"
    if issued > now + skew:
        return f"issued_at {a.issued_at} is in the future"
    try:
        if a.content_id() != a.approval_id:
            return "content does not match approval_id (edited or corrupt)"
    except (TypeError, ValueError):
        return "approval content is not canonical JSON"
    return None


def _assign_slots(slots: list[str], can: dict[str, set[str]]) -> dict[int, str]:
    """Max bipartite matching of approval slots (one per required head, labelled by role) to
    distinct people. Kuhn's augmenting paths; quorum sizes are tiny."""
    holder: dict[str, int] = {}

    def place(s: int, seen: set[str]) -> bool:
        for person in sorted(can):
            if slots[s] in can[person] and person not in seen:
                seen.add(person)
                if person not in holder or place(holder[person], seen):
                    holder[person] = s
                    return True
        return False

    for s in range(len(slots)):
        place(s, set())
    return {s: person for person, s in holder.items()}


# --------------------------------------------------------------------------- gate

def _reject(stage, sdig: str, pdig: str, reasons: list[str], evidence, approvals) -> GateDecision:
    n_ev = len(evidence) if isinstance(evidence, (list, tuple)) else 0
    n_ap = len(approvals) if isinstance(approvals, (list, tuple)) else 0
    ignored = [f"#{i}:not_evaluated:configuration_error" for i in range(n_ev)]
    ignored += [f"approval#{i}:not_evaluated:configuration_error" for i in range(n_ap)]
    return GateDecision("REJECT", stage if isinstance(stage, str) else repr(stage), sdig, pdig,
                        tuple(reasons), (), tuple(ignored))


def evaluate_gate(policy: dict, stage: str, subject: dict, evidence: list[EvidenceRecord],
                  approvals: list[Approval], requested_by: str, now: str | None = None) -> GateDecision:
    """Decide whether ``subject`` may enter ``stage``. Pure function of its inputs (plus the
    clock when ``now`` is None); never raises on bad input -- bad configuration is a REJECT."""
    config: list[str] = []
    try:
        pdig = digest(policy)
    except (TypeError, ValueError) as exc:
        pdig = ""
        config.append(f"policy:undigestable — {exc}")
    config += [f"policy:invalid — {x}" for x in policy_problems(policy)]
    sdig = ""
    if not isinstance(subject, dict) or not subject:
        config.append("request:invalid_subject — subject must be a non-empty dict")
    else:
        try:
            sdig = digest(subject)
        except (TypeError, ValueError) as exc:
            config.append(f"request:invalid_subject — not canonical JSON: {exc}")
    if not _is_str(requested_by):
        config.append("request:missing_requested_by — the requester identity is needed for "
                      "self-approval and independence checks")
    if not isinstance(evidence, (list, tuple)) or not isinstance(approvals, (list, tuple)):
        config.append("request:invalid_inputs — evidence and approvals must be lists")
    now_dt = None
    try:
        now_dt = parse_ts(now if now is not None else utcnow())
    except (TypeError, ValueError):
        config.append(f"request:invalid_now — {now!r}")
    if not config and (not isinstance(stage, str) or stage not in policy["stages"]):
        config.append(f"policy:unknown_stage — {stage!r} is not one of {sorted(policy['stages'])}")
    if config:
        return _reject(stage, sdig, pdig, config, evidence, approvals)

    defaults = policy.get("defaults", {})
    skew = timedelta(seconds=defaults.get("clock_skew_s", DEFAULT_CLOCK_SKEW_S))
    stage_cfg = policy["stages"][stage]
    checks = policy["checks"]
    required: list[str] = stage_cfg["requires"]

    ignored: list[str] = []
    valid: dict[str, list[tuple[int, EvidenceRecord]]] = {c: [] for c in required}
    unverifiable: dict[str, list[tuple[int, EvidenceRecord, str]]] = {c: [] for c in required}

    # 1. classify every record: authorise and integrity-check before anything is counted
    for idx, rec in enumerate(evidence):
        if not isinstance(rec, EvidenceRecord):
            ignored.append(f"#{idx}:not_an_evidence_record")
            continue
        rid = rec.evidence_id if _is_str(rec.evidence_id) else f"#{idx}"
        if rec.subject_digest != sdig:
            ignored.append(f"{rid}:wrong_subject")
            continue
        if not isinstance(rec.check_id, str) or rec.check_id not in valid:
            ignored.append(f"{rid}:not_required_for_stage:{rec.check_id}")
            continue
        if rec.producer not in checks[rec.check_id]["allowed_producers"]:
            ignored.append(f"{rid}:unauthorised_producer:{rec.producer}")
            continue
        if rec.producer == requested_by:
            ignored.append(f"{rid}:produced_by_requester")
            continue
        problem = _record_problem(rec, now_dt, skew)
        if problem:
            unverifiable[rec.check_id].append((idx, rec, problem))
            continue
        versions = checks[rec.check_id].get("versions")
        if versions is not None and rec.check_version not in versions:
            ignored.append(f"{rid}:check_version_not_accepted:{rec.check_version}")
            continue
        valid[rec.check_id].append((idx, rec))

    # 2. per required check: supersede chain, standing FAIL, latest record, freshness
    rejects: list[str] = []
    holds: list[str] = []
    used: list[str] = []
    for cid in required:
        cfg = checks[cid]
        producers = ", ".join(cfg["allowed_producers"])
        max_age = timedelta(seconds=cfg.get("max_age_s", defaults.get("max_age_s")))

        seen_ids: set[str] = set()
        group: list[tuple[int, EvidenceRecord]] = []
        for idx, rec in valid[cid]:
            if rec.evidence_id in seen_ids:     # same content id == same statement delivered twice
                ignored.append(f"{rec.evidence_id}:duplicate_delivery")
                continue
            seen_ids.add(rec.evidence_id)
            group.append((idx, rec))

        present = seen_ids | {r.evidence_id for _, r, _ in unverifiable[cid] if _is_str(r.evidence_id)}
        superseded_by: dict[str, str] = {}
        for _, rec in group:
            if rec.supersedes in present and rec.supersedes != rec.evidence_id:
                superseded_by.setdefault(rec.supersedes, rec.evidence_id)

        for idx, rec, problem in unverifiable[cid]:
            rid = rec.evidence_id if _is_str(rec.evidence_id) else f"#{idx}"
            if rid in superseded_by:
                ignored.append(f"{rid}:unverifiable_but_superseded_by:{superseded_by[rid]}")
                continue
            ignored.append(f"{rid}:unverifiable:{problem}")
            holds.append(f"{cid}:unverifiable — {rid} claims authorised producer {rec.producer!r} but "
                         f"{problem}; unblock: an authorised producer re-issues a valid record "
                         f"with supersedes={rid} (and investigate how it was written)")

        standing = []
        for idx, rec in group:
            if rec.evidence_id in superseded_by:
                ignored.append(f"{rec.evidence_id}:superseded_by:{superseded_by[rec.evidence_id]}")
            else:
                standing.append((idx, rec))

        fails = [(i, r) for i, r in standing if r.outcome == "FAIL"]
        if fails:
            for _, rec in fails:
                used.append(rec.evidence_id)
                lim = f" ({'; '.join(rec.limitations)})" if rec.limitations else ""
                rejects.append(f"{cid}:fail — {rec.evidence_id} by {rec.producer} at {rec.issued_at}{lim}; "
                               f"a FAIL stands until an authorised record explicitly supersedes it "
                               f"(a fixed artefact is a new subject)")
            first_fail = fails[0][1].evidence_id
            ignored += [f"{r.evidence_id}:outranked_by_standing_fail:{first_fail}"
                        for _, r in standing if r.outcome != "FAIL"]
            continue
        if not standing:
            holds.append(f"{cid}:missing — no valid record from an authorised producer; "
                         f"unblock: run the check and record its result as one of [{producers}]")
            continue

        latest_idx, latest = max(standing, key=lambda t: (parse_ts(t[1].issued_at), t[0]))
        ignored += [f"{r.evidence_id}:older_than:{latest.evidence_id}"
                    for i, r in standing if i != latest_idx]
        used.append(latest.evidence_id)
        if latest.outcome != "PASS":
            holds.append(f"{cid}:{latest.outcome.lower()} — latest record {latest.evidence_id} by "
                         f"{latest.producer} is {latest.outcome}; unblock: re-run the check and record a "
                         f"PASS as one of [{producers}] (an ERROR/INCONCLUSIVE is never a PASS)")
        elif latest.expires_at is not None and now_dt >= parse_ts(latest.expires_at):
            holds.append(f"{cid}:stale — {latest.evidence_id} expired at {latest.expires_at}; "
                         f"unblock: re-run the check")
        elif now_dt - parse_ts(latest.issued_at) > max_age:
            holds.append(f"{cid}:stale — {latest.evidence_id} issued {latest.issued_at} is older than "
                         f"max_age_s={int(max_age.total_seconds())}; unblock: re-run the check")

    # 3. approvals: bound to stage + subject, authorised role member, not self, distinct people
    reqs = stage_cfg.get("approvals", [])
    req_roles = {a["role"]: a for a in reqs}
    roles_cfg = policy.get("roles")
    can: dict[str, set[str]] = {}
    ids_by_key: dict[tuple[str, str], str] = {}
    refused: dict[str, list[str]] = {r: [] for r in req_roles}
    for idx, a in enumerate(approvals):
        if not isinstance(a, Approval):
            ignored.append(f"approval#{idx}:not_an_approval")
            continue
        aid = a.approval_id if _is_str(a.approval_id) else f"approval#{idx}"
        if a.subject_digest != sdig:
            ignored.append(f"{aid}:wrong_subject")
            continue
        if a.stage != stage:
            ignored.append(f"{aid}:other_stage:{a.stage}")
            continue
        if not isinstance(a.role, str) or a.role not in req_roles:
            ignored.append(f"{aid}:role_not_required:{a.role}")
            continue
        problem = _approval_problem(a, now_dt, skew)
        if problem:
            ignored.append(f"{aid}:unverifiable:{problem}")
            continue
        if roles_cfg is not None and a.approver not in roles_cfg[a.role]["members"]:
            ignored.append(f"{aid}:approver_not_member_of:{a.role}")
            continue
        if req_roles[a.role].get("no_self_approval", True) and a.approver in (requested_by, a.requested_by):
            ignored.append(f"{aid}:self_approval_refused")
            refused[a.role].append(aid)
            continue
        key = (a.approver, a.role)
        if key in ids_by_key:
            ignored.append(f"{aid}:duplicate_of:{ids_by_key[key]}")
            continue
        ids_by_key[key] = aid
        can.setdefault(a.approver, set()).add(a.role)

    slots = [a["role"] for a in reqs for _ in range(a["count"])]
    assignment = _assign_slots(slots, can)
    counted = {(person, slots[s]) for s, person in assignment.items()}
    approval_ids = [aid for key, aid in ids_by_key.items() if key in counted]
    ignored += [f"{aid}:not_counted:person_already_counted_or_quorum_met"
                for key, aid in ids_by_key.items() if key not in counted]
    for a in reqs:
        filled = sum(1 for s in assignment if slots[s] == a["role"])
        if filled < a["count"]:
            note = f"; refused self-approvals: {', '.join(refused[a['role']])}" if refused[a["role"]] else ""
            holds.append(f"approval:{a['role']}:missing — {filled}/{a['count']} approvals by distinct people "
                         f"other than the requester {requested_by!r}{note}; unblock: an authorised "
                         f"{a['role']} approves this subject for stage {stage}")

    verdict = "REJECT" if rejects else "HOLD" if holds else "ALLOW"
    return GateDecision(verdict, stage, sdig, pdig, tuple(rejects + holds), tuple(used),
                        tuple(ignored), tuple(approval_ids))


def decide(ws: Workspace, stage: str, subject: dict, requested_by: str, *,
           policy: dict | None = None, policy_path: str | Path | None = None,
           now: str | None = None) -> GateDecision:
    """Gate ``subject`` using the workspace registry and append the decision to
    ``registry/gate_decisions.jsonl``. A policy that fails to load is a logged REJECT.
    A corrupt evidence/approval log raises ``EvidenceStoreError`` (it needs a human)."""
    decided_at = now if now is not None else iso_utc(utcnow())   # one clock reading for gate + log
    try:
        sdig = digest(subject) if isinstance(subject, dict) and subject else ""
    except (TypeError, ValueError):
        sdig = ""
    if policy is None:
        try:
            policy = load_policy(policy_path)
        except (PolicyError, OSError) as exc:
            decision = GateDecision("REJECT", stage, sdig, "", (f"policy:load_error — {exc}",), (), ())
            append_jsonl(Path(ws.registry) / DECISION_LOG,
                         {"decided_at": decided_at, "requested_by": requested_by, **decision.to_dict()})
            return decision
    evidence = EvidenceStore(ws).for_subject(sdig) if sdig else []
    approvals = ApprovalStore(ws).for_subject(sdig) if sdig else []
    decision = evaluate_gate(policy, stage, subject, evidence, approvals, requested_by, now=decided_at)
    append_jsonl(Path(ws.registry) / DECISION_LOG,
                 {"decided_at": decided_at, "requested_by": requested_by, **decision.to_dict()})
    return decision
