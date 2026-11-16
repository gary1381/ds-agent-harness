"""in-toto export: evidence records, gate decisions and answer certificates become Statement v1
JSON bound to the subject's sha256 digest, marked UNSIGNED-LOCAL; edited records are refused."""
from __future__ import annotations

import base64
from dataclasses import replace
import json
from pathlib import Path

import pytest

from dsharness import cli, tools as T
from dsharness.common import digest, file_digest, read_jsonl
from dsharness.evidence import EvidenceStore, decide
from dsharness.evidence.attest import (CERTIFICATE_PREDICATE, DSSE_PAYLOAD_TYPE, EVIDENCE_PREDICATE, GATE_PREDICATE,
                                       STATEMENT_TYPE, UNSIGNED, AttestError, evidence_statement, export_registry,
                                       gate_statement, is_signed, registry_statements, statement_body)
from dsharness.workspace import Workspace

SUBJECT = {"kind": "candidate", "candidate_id": "c-attest", "build_id": "b1"}


def private_ws(base: Workspace, root: Path) -> Workspace:
    root.mkdir(parents=True)
    (root / "lake").symlink_to(base.lake, target_is_directory=True)
    (root / "sources").symlink_to(base.sources, target_is_directory=True)
    (root / "custody").mkdir()
    return Workspace(root).ensure()


@pytest.fixture(scope="module")
def ws(small_ws, tmp_path_factory) -> Workspace:
    """A registry with evidence, gate decisions and answer certificates produced by the real tools."""
    w = private_ws(small_ws, tmp_path_factory.mktemp("attest") / "ws")
    ctx = T.make_context(w, "score", "analyst", "agent:attest")
    try:
        q = T.query_metric(ctx, "net_revenue_fen", ["region"], ["2026-03", "2026-03"])
        T.submit_answer(ctx, "What was net revenue by region for March 2026?", q["rows"], metric_call=q["metric_call"])
        T.submit_answer(ctx, "How many customers signed up in 2025?", [{"value": 1}])
    finally:
        ctx.close()
    EvidenceStore(w).record(check_id="model.sealed_eval", check_version="1", stage="model_shadow", subject=SUBJECT,
                            outcome="PASS", metrics={"auc": 0.7}, producer="evaluator:custody")
    decide(w, "model_shadow", SUBJECT, requested_by="agent:ds")
    return w


def test_evidence_statement_binds_the_record_to_its_subject(ws):
    rec = next(r for r in EvidenceStore(ws).all() if r.subject == SUBJECT)
    st = evidence_statement(rec)
    assert st["_type"] == STATEMENT_TYPE and st["predicateType"] == EVIDENCE_PREDICATE
    assert st["subject"] == [{"name": f"dsharness/candidate/{digest(SUBJECT)[:16]}",
                              "digest": {"sha256": digest(SUBJECT)}}]
    assert st["predicate"] == rec.to_dict() and st["signature"] == UNSIGNED
    assert set(statement_body(st)) == {"_type", "subject", "predicateType", "predicate"}
    assert not is_signed(st) and not is_signed(statement_body(st))
    json.dumps(st, allow_nan=False)


def test_edited_records_are_refused_not_laundered(ws):
    rec = next(r for r in EvidenceStore(ws).all() if r.subject == SUBJECT)
    with pytest.raises(AttestError, match="content id mismatch"):
        evidence_statement(replace(rec, outcome="FAIL"))
    forged = replace(rec, subject={**SUBJECT, "candidate_id": "other"})
    with pytest.raises(AttestError):
        evidence_statement(replace(forged, evidence_id=forged.content_id()))     # id fixed, digest still stale
    with pytest.raises(AttestError, match="no subject digest"):
        gate_statement({"verdict": "REJECT", "stage": "x", "subject_digest": "", "reasons": ["policy:load_error"]})
    with pytest.raises(AttestError, match="not a gate decision"):
        gate_statement({"verdict": "MAYBE"})


def test_registry_export_covers_evidence_decisions_and_certificates(ws, tmp_path):
    stmts, problems = registry_statements(ws)
    assert problems == []
    by_type = {}
    for rel, st in stmts:
        by_type.setdefault(st["predicateType"], []).append((rel, st))
        assert st["signature"] == UNSIGNED and len(st["subject"][0]["digest"]["sha256"]) == 64
    evidence = EvidenceStore(ws).all()
    decisions = read_jsonl(ws.registry / "gate_decisions.jsonl")
    certs = read_jsonl(ws.registry / "answer_certificates.jsonl")
    assert len(by_type[EVIDENCE_PREDICATE]) == len(evidence) == 7
    assert len(by_type[GATE_PREDICATE]) == len(decisions) == 3
    assert len(by_type[CERTIFICATE_PREDICATE]) == len(certs) == 2
    gate = by_type[GATE_PREDICATE][-1][1]
    assert gate["subject"][0]["digest"]["sha256"] == digest(SUBJECT)
    assert {k: v for k, v in gate["predicate"].items() if k != "decision_id"} == decisions[-1]
    assert gate["predicate"]["verdict"] == "HOLD"          # a statement records the verdict, it does not grant one
    assert gate["predicate"]["decision_id"].startswith("gd-")
    cert_st = by_type[CERTIFICATE_PREDICATE][0][1]
    assert cert_st["predicate"] == certs[0]
    assert cert_st["subject"][0]["digest"]["sha256"] == certs[0]["gate"]["subject_digest"]

    out = tmp_path / "statements"
    res = export_registry(ws, out)
    assert res["problems"] == [] and res["n_statements"] == 12
    assert res["counts"] == {"evidence": 7, "gate": 3, "answers": 2}
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["signature"] == UNSIGNED and len(manifest["files"]) == 12
    for rel, sha in manifest["files"].items():
        assert file_digest(out / rel) == sha
        assert json.loads((out / rel).read_text())["_type"] == STATEMENT_TYPE
    for zone in (ws.registry, ws.custody, ws.registry / "nested"):
        with pytest.raises(AttestError, match="refusing"):
            export_registry(ws, zone)


def test_is_signed_is_structural_and_never_true_for_local_statements(ws):
    st = registry_statements(ws)[0][0][1]
    envelope = {"payloadType": DSSE_PAYLOAD_TYPE,
                "payload": base64.b64encode(json.dumps(statement_body(st)).encode()).decode(),
                "signatures": [{"keyid": "", "sig": "MEUCIQ..."}]}
    assert is_signed(envelope)
    assert not is_signed({**envelope, "signatures": []})
    assert not is_signed({**envelope, "signatures": [{"sig": ""}]})
    assert not is_signed({**envelope, "payloadType": "application/json"})
    assert not is_signed({**envelope, "signature": UNSIGNED})
    assert not is_signed(st) and not is_signed(None) and not is_signed("signed")


def test_cli_export_reports_unexportable_lines(ws, tmp_path, capsys):
    out = tmp_path / "ok"
    assert cli.main(["evidence", "export", "--workspace", str(ws.root), "--out", str(out)]) == 0
    assert "12 in-toto statements (UNSIGNED-LOCAL)" in capsys.readouterr().out
    assert cli.main(["evidence", "export", "--workspace", str(ws.root), "--out", str(ws.registry / "x")]) == 2
    # an edited evidence line (outcome flipped, id kept) cannot be exported: exit 1, reported, not written
    bad = private_ws(Workspace(ws.root), tmp_path / "edited")
    lines = (ws.registry / "evidence.jsonl").read_text().splitlines()
    first = json.loads(lines[0])
    first["outcome"] = "PASS" if first["outcome"] != "PASS" else "FAIL"
    (bad.registry / "evidence.jsonl").write_text("\n".join([json.dumps(first), *lines[1:]]) + "\n")
    assert cli.main(["evidence", "export", "--workspace", str(bad.root), "--out", str(tmp_path / "bad")]) == 1
    err = capsys.readouterr().err
    assert "content id mismatch" in err and first["evidence_id"] in err
    assert not (tmp_path / "bad" / "evidence" / f"{first['evidence_id']}.intoto.json").exists()
    assert json.loads((tmp_path / "bad" / "manifest.json").read_text())["problems"]
