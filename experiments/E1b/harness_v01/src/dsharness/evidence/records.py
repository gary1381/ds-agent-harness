"""Evidence and approval records: append-only JSON lines in the registry zone.

Trust boundary: the stores are *transport*, not truth. Anyone who can write to
``registry/`` can append a line that claims any producer, so nothing here decides
whether a record counts -- that is the gate's job (``evidence.gate``), which
authorises producers and checks integrity *before* counting anything. What the
stores do guarantee:

  * history is never rewritten (append only; a correction is a new record that
    ``supersedes`` the old one, so the original failure stays visible);
  * every record carries a content-derived id (``ev-``/``ap-`` + sha256 of its body),
    so an edited line no longer matches its id and the gate treats it as unverifiable;
  * outcomes are one of PASS | FAIL | INCONCLUSIVE | ERROR, never booleans;
  * a corrupt line raises ``EvidenceStoreError`` with its line number instead of
    being skipped.

The content id is an integrity check against accidents and casual edits, not a
signature: whoever can write the file can recompute it. Production replaces it with
in-toto statements signed by the producer's CI OIDC identity (Sigstore), see
``policies/gates.yaml``. ``evidence.attest`` exports records as such statements, marked
UNSIGNED-LOCAL until CI signs them (prod stages treat unsigned evidence as HOLD).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path

from ..common import append_jsonl, canonical_json, digest, parse_ts, utcnow
from ..workspace import Workspace

OUTCOMES = ("PASS", "FAIL", "INCONCLUSIVE", "ERROR")


class EvidenceStoreError(RuntimeError):
    """A registry file is corrupt (unparseable line, missing/extra fields)."""


def iso_utc(ts: str | datetime) -> str:
    """Normalise a timestamp to ISO-8601 UTC with an explicit +00:00 offset."""
    dt = parse_ts(ts) if isinstance(ts, str) else ts
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _json_default(obj):
    # numpy / pandas scalars and arrays expose tolist(); keep numbers numeric.
    if hasattr(obj, "tolist"):
        return obj.tolist()
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError(f"metrics value of type {type(obj).__name__} is not JSON-serialisable")


def _plain_metrics(metrics: dict) -> dict:
    if not isinstance(metrics, dict):
        raise TypeError(f"metrics must be a dict, got {type(metrics).__name__}")
    try:
        text = json.dumps(metrics, allow_nan=False, default=_json_default, sort_keys=True)
    except ValueError as exc:   # NaN / inf: JSON cannot carry them, and None is the honest "undefined"
        raise ValueError(f"metrics must be finite (use None for undefined values): {exc}") from exc
    return json.loads(text)


def _plain_subject(subject: dict) -> dict:
    # Same canonicalisation as common.digest, so digest(stored subject) == digest(caller's subject).
    if not isinstance(subject, dict) or not subject:
        raise ValueError("subject must be a non-empty dict")
    plain = json.loads(canonical_json(subject))
    if digest(plain) != digest(subject):    # e.g. non-string keys sort differently once stringified
        raise ValueError("subject does not survive a JSON round trip; use string keys and plain values")
    return plain


def _require_str(name: str, value, *, optional: bool = False) -> None:
    if optional and value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string, got {value!r}")


def _from_dict(cls, d: dict, where: str):
    names = {f.name for f in fields(cls)}
    if not isinstance(d, dict) or set(d) != names:
        got = sorted(d) if isinstance(d, dict) else type(d).__name__
        raise EvidenceStoreError(f"{where}: expected fields {sorted(names)}, got {got}")
    return cls(**d)


def _load(path: Path, cls) -> list:
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError as exc:
                raise EvidenceStoreError(f"{path}:{lineno}: not valid JSON ({exc.msg})") from exc
            out.append(_from_dict(cls, d, f"{path}:{lineno}"))
    return out


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    check_id: str
    check_version: str
    stage: str              # stage the check was run for; informational -- the gate binds on subject + check
    subject: dict           # e.g. {"kind":"candidate","candidate_id":..,"build_id":..,"semantic_digest":..}
    subject_digest: str     # digest(subject)
    outcome: str            # PASS | FAIL | INCONCLUSIVE | ERROR
    metrics: dict
    producer: str           # identity claim, e.g. "evaluator:custody"; authorised by the gate policy
    issued_at: str
    expires_at: str | None
    limitations: list[str]
    supersedes: str | None = None

    def body(self) -> dict:
        d = asdict(self)
        d.pop("evidence_id")
        return d

    def content_id(self) -> str:
        return "ev-" + digest(self.body())[:24]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "EvidenceRecord":
        return _from_dict(cls, d, "evidence record")

    @classmethod
    def create(cls, *, check_id: str, check_version: str, stage: str, subject: dict, outcome: str,
               metrics: dict, producer: str, issued_at: str, expires_at: str | None = None,
               limitations=(), supersedes: str | None = None) -> "EvidenceRecord":
        """Validate inputs and build a record with its content-derived id."""
        for name, value in (("check_id", check_id), ("check_version", check_version),
                            ("stage", stage), ("producer", producer)):
            _require_str(name, value)
        _require_str("supersedes", supersedes, optional=True)
        if not isinstance(outcome, str) or outcome not in OUTCOMES:
            raise ValueError(f"outcome must be one of {OUTCOMES}, got {outcome!r}")
        if isinstance(limitations, str):
            raise ValueError("limitations must be a sequence of strings, not one string")
        limitations = list(limitations)
        if not all(isinstance(x, str) for x in limitations):
            raise ValueError("limitations must be a sequence of strings")
        issued = iso_utc(issued_at)
        expires = iso_utc(expires_at) if expires_at is not None else None
        if expires is not None and parse_ts(expires) <= parse_ts(issued):
            raise ValueError(f"expires_at {expires} is not after issued_at {issued}")
        plain_subject = _plain_subject(subject)
        draft = cls(evidence_id="", check_id=check_id, check_version=check_version, stage=stage,
                    subject=plain_subject, subject_digest=digest(plain_subject), outcome=outcome,
                    metrics=_plain_metrics(metrics), producer=producer, issued_at=issued,
                    expires_at=expires, limitations=limitations, supersedes=supersedes)
        return cls(**{**asdict(draft), "evidence_id": draft.content_id()})


class EvidenceStore:
    """Append-only evidence log at ``registry/evidence.jsonl``."""

    def __init__(self, ws: Workspace):
        self.ws = ws
        self.path = Path(ws.registry) / "evidence.jsonl"

    def record(self, *, check_id: str, check_version: str, stage: str, subject: dict, outcome: str,
               metrics: dict, producer: str, ttl_s: int | None = None, limitations=(),
               supersedes: str | None = None) -> EvidenceRecord:
        """Append one check result. Identical content at the same second is an idempotent replay
        (the existing record is returned, nothing is appended)."""
        if ttl_s is not None and (type(ttl_s) is not int or ttl_s <= 0):
            raise ValueError(f"ttl_s must be a positive int, got {ttl_s!r}")
        issued = iso_utc(utcnow())
        expires = iso_utc(parse_ts(issued) + timedelta(seconds=ttl_s)) if ttl_s is not None else None
        rec = EvidenceRecord.create(check_id=check_id, check_version=check_version, stage=stage,
                                    subject=subject, outcome=outcome, metrics=metrics, producer=producer,
                                    issued_at=issued, expires_at=expires, limitations=limitations,
                                    supersedes=supersedes)
        existing = {r.evidence_id: r for r in self.all()}
        if rec.evidence_id in existing:
            return existing[rec.evidence_id]
        if supersedes is not None:
            target = existing.get(supersedes)
            if target is None:
                raise ValueError(f"supersedes={supersedes!r}: no such evidence record")
            if (target.subject_digest, target.check_id) != (rec.subject_digest, rec.check_id):
                raise ValueError(f"supersedes={supersedes!r} is for a different subject or check "
                                 f"({target.check_id}, {target.subject_digest[:12]})")
        append_jsonl(self.path, rec.to_dict())
        return rec

    def all(self) -> list[EvidenceRecord]:
        return _load(self.path, EvidenceRecord)

    def for_subject(self, subject_digest: str) -> list[EvidenceRecord]:
        """Records *claiming* this subject, in file order. The gate re-verifies the binding."""
        return [r for r in self.all() if r.subject_digest == subject_digest]


@dataclass(frozen=True)
class Approval:
    approval_id: str
    stage: str
    subject_digest: str
    approver: str           # identity claim of the human approver
    role: str               # e.g. business_owner | risk
    requested_by: str       # who asked for the promotion this approval answers
    issued_at: str

    def body(self) -> dict:
        d = asdict(self)
        d.pop("approval_id")
        return d

    def content_id(self) -> str:
        return "ap-" + digest(self.body())[:24]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Approval":
        return _from_dict(cls, d, "approval")

    @classmethod
    def create(cls, *, stage: str, subject_digest: str, approver: str, role: str,
               requested_by: str, issued_at: str) -> "Approval":
        for name, value in (("stage", stage), ("subject_digest", subject_digest), ("approver", approver),
                            ("role", role), ("requested_by", requested_by)):
            _require_str(name, value)
        draft = cls("", stage, subject_digest, approver, role, requested_by, iso_utc(issued_at))
        return cls(**{**asdict(draft), "approval_id": draft.content_id()})


class ApprovalStore:
    """Append-only approval log at ``registry/approvals.jsonl``.

    Recording is a statement of fact ("X approved S for stage T"); whether it counts --
    role membership, no self-approval, distinct people -- is decided by the gate, so a
    refused self-approval stays visible in the audit trail instead of vanishing.
    """

    def __init__(self, ws: Workspace):
        self.ws = ws
        self.path = Path(ws.registry) / "approvals.jsonl"

    def record(self, *, stage: str, approver: str, role: str, requested_by: str,
               subject: dict | None = None, subject_digest: str | None = None) -> Approval:
        """Bind an approval to a subject: pass exactly one of ``subject`` or ``subject_digest``."""
        if (subject is None) == (subject_digest is None):
            raise ValueError("pass exactly one of subject or subject_digest")
        sd = digest(_plain_subject(subject)) if subject is not None else subject_digest
        appr = Approval.create(stage=stage, subject_digest=sd, approver=approver, role=role,
                               requested_by=requested_by, issued_at=utcnow())
        if any(a.approval_id == appr.approval_id for a in self.all()):
            return appr     # idempotent replay of the same approval in the same second
        append_jsonl(self.path, appr.to_dict())
        return appr

    def all(self) -> list[Approval]:
        return _load(self.path, Approval)

    def for_subject(self, subject_digest: str) -> list[Approval]:
        return [a for a in self.all() if a.subject_digest == subject_digest]
