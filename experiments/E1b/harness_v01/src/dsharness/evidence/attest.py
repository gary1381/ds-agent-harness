"""Export evidence records, gate decisions and answer certificates as in-toto Statement v1 JSON.

Trust boundary: this module only *re-packages* what the registry already holds; it decides
nothing and signs nothing. A statement binds a predicate (the record body) to a subject by the
subject's sha256 digest (``dsharness.common.digest`` of the subject the record names), using the
in-toto attestation framework's Statement layer:

    {"_type": "https://in-toto.io/Statement/v1",
     "subject": [{"name": "dsharness/<kind>/<digest prefix>", "digest": {"sha256": <subject digest>}}],
     "predicateType": "https://dsharness.dev/evidence/v1" | ".../gate-decision/v1" | ".../answer-certificate/v1",
     "predicate": <record body>,
     "signature": "UNSIGNED-LOCAL"}

The ``signature`` marker sits outside the Statement layer (``statement_body`` strips it). Locally
produced statements are UNSIGNED-LOCAL: anyone who can write ``registry/`` can produce them.

Production: each producer's CI job signs its statements with the job's OIDC identity -- GitHub
``actions/attest`` (Sigstore, verified with ``gh attestation verify --signer-workflow``) or, on a
self-hosted GitLab, ``cosign attest`` with a KMS key -- inside a DSSE envelope. The gate's
producer strings then map to verified signer identities. Until that is in place:

  * an UNSIGNED-LOCAL statement (``is_signed`` is False) MUST be treated as HOLD in any prod
    stage (answer publication outside the team, pipeline merge to prod, model scale-up), whatever
    the local gate said. The reference gate policy is not changed by this module;
  * ``is_signed`` is structural only: True means "carries a DSSE signature to verify", never
    "verified". Verification is the signer tool's job (cosign / gh attestation verify).
"""
from __future__ import annotations

import json
from pathlib import Path

from ..common import canonical_json, digest, file_digest, read_jsonl
from ..workspace import Workspace
from .gate import DECISION_LOG
from .records import EvidenceRecord, EvidenceStore, EvidenceStoreError

STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
EVIDENCE_PREDICATE = "https://dsharness.dev/evidence/v1"
GATE_PREDICATE = "https://dsharness.dev/gate-decision/v1"
CERTIFICATE_PREDICATE = "https://dsharness.dev/answer-certificate/v1"
UNSIGNED = "UNSIGNED-LOCAL"
DSSE_PAYLOAD_TYPE = "application/vnd.in-toto+json"
CERTIFICATE_LOG = "answer_certificates.jsonl"     # written by tools.analytics.submit_answer
_SHA256_HEX = set("0123456789abcdef")


class AttestError(ValueError):
    """A record cannot be exported faithfully (integrity mismatch, missing subject digest)."""


def _check_sha256(value, what: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or set(value) - _SHA256_HEX:
        raise AttestError(f"{what}: {value!r} is not a sha256 hex digest")
    return value


def statement(subject_name: str, subject_digest: str, predicate_type: str, predicate: dict) -> dict:
    """An in-toto Statement v1 over one subject, marked UNSIGNED-LOCAL."""
    if not isinstance(predicate, dict) or not predicate:
        raise AttestError("predicate must be a non-empty object")
    return {"_type": STATEMENT_TYPE,
            "subject": [{"name": subject_name, "digest": {"sha256": _check_sha256(subject_digest, subject_name)}}],
            "predicateType": predicate_type, "predicate": json.loads(canonical_json(predicate)),
            "signature": UNSIGNED}


def statement_body(stmt: dict) -> dict:
    """The in-toto Statement proper (what a signer wraps in a DSSE envelope): marker removed."""
    return {k: stmt[k] for k in ("_type", "subject", "predicateType", "predicate")}


def evidence_statement(rec: EvidenceRecord) -> dict:
    """Statement for one evidence record. An edited record (content id or subject digest no longer
    matching) is refused: exporting it would launder an unverifiable record into an attestation."""
    if rec.content_id() != rec.evidence_id:
        raise AttestError(f"{rec.evidence_id}: content id mismatch (the record was edited)")
    if digest(rec.subject) != rec.subject_digest:
        raise AttestError(f"{rec.evidence_id}: subject digest does not match the subject")
    kind = str(rec.subject.get("kind", "subject"))
    return statement(f"dsharness/{kind}/{rec.subject_digest[:16]}", rec.subject_digest, EVIDENCE_PREDICATE,
                     rec.to_dict())


def gate_decision_id(decision: dict) -> str:
    return "gd-" + digest(decision)[:24]


def gate_statement(decision: dict) -> dict:
    """Statement for one logged gate decision (a line of registry/gate_decisions.jsonl)."""
    if not isinstance(decision, dict) or decision.get("verdict") not in ("ALLOW", "HOLD", "REJECT"):
        raise AttestError(f"not a gate decision: {str(decision)[:120]}")
    sdig = decision.get("subject_digest")
    if not sdig:
        raise AttestError(f"{gate_decision_id(decision)}: decision has no subject digest (malformed subject)")
    return statement(f"dsharness/{decision.get('stage')}/{str(sdig)[:16]}", sdig, GATE_PREDICATE,
                     {"decision_id": gate_decision_id(decision), **decision})


def certificate_statement(cert: dict) -> dict:
    """Statement for one answer certificate; the subject is the answer it certifies."""
    gate = cert.get("gate") if isinstance(cert, dict) else None
    if not isinstance(gate, dict) or not cert.get("certificate_id"):
        raise AttestError(f"not an answer certificate: {str(cert)[:120]}")
    return statement(f"dsharness/answer/{str(gate.get('subject_digest'))[:16]}", gate.get("subject_digest"),
                     CERTIFICATE_PREDICATE, cert)


def is_signed(doc) -> bool:
    """True only for a DSSE envelope carrying an in-toto payload and at least one signature.

    Structural, not cryptographic: a True result still has to be verified (cosign / gh attestation
    verify) before it counts. UNSIGNED-LOCAL statements and bare statements are False, and prod
    stages treat them as HOLD."""
    if not isinstance(doc, dict) or doc.get("signature") == UNSIGNED:
        return False
    sigs = doc.get("signatures")
    return (doc.get("payloadType") == DSSE_PAYLOAD_TYPE and isinstance(doc.get("payload"), str)
            and isinstance(sigs, list) and bool(sigs)
            and all(isinstance(s, dict) and isinstance(s.get("sig"), str) and s["sig"] for s in sigs))


def registry_statements(ws: Workspace) -> tuple[list[tuple[str, dict]], list[str]]:
    """(relative file name, statement) for every evidence record, gate decision and answer certificate
    in the workspace registry, plus the problems that prevented exporting a line."""
    out: list[tuple[str, dict]] = []
    problems: list[str] = []
    try:
        records = EvidenceStore(ws).all()
    except EvidenceStoreError as exc:
        records, problems = [], [f"evidence log unreadable: {exc}"]
    for rec in records:
        try:
            out.append((f"evidence/{rec.evidence_id}.intoto.json", evidence_statement(rec)))
        except AttestError as exc:
            problems.append(str(exc))
    for name, kind, build in ((DECISION_LOG, "gate", gate_statement),
                              (CERTIFICATE_LOG, "answers", certificate_statement)):
        try:
            lines = read_jsonl(ws.registry / name)
        except json.JSONDecodeError as exc:
            problems.append(f"{name} unreadable: {exc}")
            continue
        for i, line in enumerate(lines, start=1):
            try:
                stmt = build(line)
            except AttestError as exc:
                problems.append(f"{name}:{i}: {exc}")
                continue
            key = stmt["predicate"].get("decision_id") or stmt["predicate"].get("certificate_id")
            out.append((f"{kind}/{key}.intoto.json", stmt))
    return out, problems


def export_registry(ws: Workspace, out_dir: str | Path) -> dict:
    """Write one statement file per record under `out_dir` plus `manifest.json` (file -> sha256).

    Refuses to write into the registry or custody zones. Returns a summary with the problems; the
    caller decides how loudly to fail (the CLI exits non-zero when anything could not be exported)."""
    out = Path(out_dir).resolve()
    for zone in (ws.registry, ws.custody):
        z = Path(zone).resolve()
        if out == z or z in out.parents:
            raise AttestError(f"refusing to write statements into the {z.name}/ zone ({out})")
    statements, problems = registry_statements(ws)
    files = {}
    for rel, stmt in statements:
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(json.dumps(stmt, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8") + b"\n")
        files[rel] = file_digest(path)
    manifest = {"statement_type": STATEMENT_TYPE, "signature": UNSIGNED, "workspace": str(ws.root),
                "counts": {k: sum(1 for r in files if r.startswith(f"{k}/")) for k in ("evidence", "gate", "answers")},
                "files": files, "problems": problems,
                "note": "UNSIGNED-LOCAL: sign in CI with the producer's OIDC identity (actions/attest or cosign); "
                        "prod stages treat unsigned statements as HOLD"}
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"out_dir": str(out), "n_statements": len(files), "counts": manifest["counts"], "problems": problems}
