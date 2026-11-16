"""Readout gate tests. Each test writes a small run folder in the layout the gate reads and checks
the verdict the gate gives for one known defect (scored error rows, mixed judges, one run, ...)."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import random

import pytest
import yaml

from dsharness.cli import main as cli_main
from dsharness.evidence import load_policy, policy_problems
from dsharness.readout import CHECKS, Spec, decide
from dsharness.readout.core import estimate, load_rows, paired_deltas

N = 60


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def pm_run(root: Path, name: str, acc: dict[str, float], *, seed: int, judge="j1", model="m1",
           errors: dict[str, int] | None = None, judge_errors=False, same_answers=False, judged="judged.jsonl"):
    """One run folder: per arm, N items correct with probability acc[arm]."""
    rng = random.Random(seed)
    ans, jud = [], []
    for arm, p in acc.items():
        for i in range(N):
            aid = f"{arm}|4096|q{i}"
            err = i < (errors or {}).get(arm, 0)
            ans.append({"id": aid, "answer": f"a{i}" if same_answers else f"{arm}{i}{seed}",
                        "error": "boom" if err else None, "model": model})
            if not err or judge_errors:
                jud.append({"id": aid, "label": "CORRECT" if rng.random() < p else "WRONG", "judge": judge})
    _write(root / name / "answers.jsonl", ans)
    _write(root / name / judged, jud)


def spec(root: Path, **kw) -> Spec:
    base = dict(claim_id="t", claim="t beats c", claimed_verdict="passed", treatment="t", control="c",
                runs=["r1_lc", "r2_lc"], root=root)
    return Spec(**{**base, **kw})


def outcome(d: dict, check: str) -> str:
    return next(c["outcome"] for c in d["checks"] if c["check"] == check)


def test_policy_has_readout_stage():
    pol = load_policy()
    assert policy_problems(pol) == []
    assert set(pol["stages"]["readout_publish"]["requires"]) == set(CHECKS)


def test_clean_large_effect_is_allowed(tmp_path):
    for k in (1, 2):
        pm_run(tmp_path, f"r{k}_lc", {"t": 0.9, "c": 0.3}, seed=k)
    d = decide(spec(tmp_path))
    assert d["verdict"] == "ALLOW", d


def test_scored_error_rows_reject(tmp_path):
    for k in (1, 2):
        pm_run(tmp_path, f"r{k}_lc", {"t": 0.9, "c": 0.3}, seed=k, errors={"c": 5}, judge_errors=True)
    d = decide(spec(tmp_path))
    assert d["verdict"] == "REJECT"
    assert outcome(d, "readout.errors_excluded") == "FAIL"


def test_unscored_error_rows_pass(tmp_path):
    for k in (1, 2):
        pm_run(tmp_path, f"r{k}_lc", {"t": 0.9, "c": 0.3}, seed=k, errors={"c": 5})
    assert outcome(decide(spec(tmp_path)), "readout.errors_excluded") == "PASS"


def test_mixed_judges_reject(tmp_path):
    pm_run(tmp_path, "r1_lc", {"t": 0.9}, seed=1, judge="j1")
    pm_run(tmp_path, "r1_lc_c", {"c": 0.3}, seed=1, judge="j2")
    s = spec(tmp_path, runs=["r1_lc"], control_runs=["r1_lc_c"])
    d = decide(s)
    assert outcome(d, "readout.same_judge") == "FAIL" and d["verdict"] == "REJECT"


def test_comparison_about_the_judge_is_not_a_judge_mismatch(tmp_path):
    for k in (1, 2):
        pm_run(tmp_path, f"r{k}_lc", {"t": 0.9}, seed=k, judge="j1")
        pm_run(tmp_path, f"r{k}_lc_c", {"c": 0.3}, seed=k, judge="j2")
    s = spec(tmp_path, compare_about="judge", runs=["r1_lc", "r2_lc"], control_runs=["r1_lc_c", "r2_lc_c"])
    assert outcome(decide(s), "readout.same_judge") == "PASS"


def test_single_run_holds(tmp_path):
    pm_run(tmp_path, "r1_lc", {"t": 0.9, "c": 0.3}, seed=1)
    d = decide(spec(tmp_path, runs=["r1_lc"]))
    assert d["verdict"] == "HOLD"
    assert outcome(d, "readout.repeat_runs") == "INCONCLUSIVE"


def test_identical_answers_are_a_wiring_fault(tmp_path):
    for k in (1, 2):
        pm_run(tmp_path, f"r{k}_lc", {"t": 0.5, "c": 0.5}, seed=k, same_answers=True)
    d = decide(spec(tmp_path, claimed_verdict="not_passed"))
    assert outcome(d, "readout.no_wiring_fault") == "FAIL" and d["verdict"] == "REJECT"


def test_not_passed_contradicted_by_interval_rejects(tmp_path):
    for k in (1, 2):
        pm_run(tmp_path, f"r{k}_lc", {"t": 0.95, "c": 0.2}, seed=k)
    d = decide(spec(tmp_path, claimed_verdict="not_passed"))
    assert outcome(d, "readout.interval") == "FAIL"


def test_equal_needs_interval_inside_margin(tmp_path):
    for k in (1, 2):
        pm_run(tmp_path, f"r{k}_lc", {"t": 0.6, "c": 0.6}, seed=k)
    d = decide(spec(tmp_path, claimed_verdict="equal", margin_points=1.0))
    assert outcome(d, "readout.interval") == "INCONCLUSIVE" and d["verdict"] == "HOLD"


def _small_effect(tmp_path, second: dict[str, float] | None):
    """t beats c by 1 point (8 of 800 items) under judge 1 in both runs; under judge 2 the gain is gone."""
    for k in (1, 2):
        rows_a, rows_j, rows_2 = [], [], []
        for arm in ("t", "c"):
            for i in range(800):
                aid = f"{arm}|4096|q{i}"
                rows_a.append({"id": aid, "answer": f"{arm}{i}", "error": None, "model": "m1"})
                ok = i % 2 == 0 or (arm == "t" and i % 100 == 1)
                rows_j.append({"id": aid, "label": "CORRECT" if ok else "WRONG", "judge": "j1"})
                if second is not None:
                    ok2 = i % 2 == 0 or (arm == "t" and i % 800 == 1) or (arm == "c" and i % 800 == 3)
                    rows_2.append({"id": aid, "label": "CORRECT" if ok2 else "WRONG", "judge": "j2"})
        _write(tmp_path / f"r{k}_lc" / "answers.jsonl", rows_a)
        _write(tmp_path / f"r{k}_lc" / "judged.jsonl", rows_j)
        if second is not None:
            _write(tmp_path / f"r{k}_lc" / "judged_2.jsonl", rows_2)


def test_small_passed_delta_needs_a_second_judge(tmp_path):
    _small_effect(tmp_path, None)
    d = decide(spec(tmp_path))
    assert outcome(d, "readout.interval") == "PASS"
    assert outcome(d, "readout.second_judge") == "INCONCLUSIVE" and d["verdict"] == "HOLD"


def test_small_passed_delta_that_vanishes_under_second_judge_rejects(tmp_path):
    _small_effect(tmp_path, {})
    d = decide(spec(tmp_path, second_judged_file="judged_2.jsonl"))
    assert outcome(d, "readout.second_judge") == "FAIL" and d["verdict"] == "REJECT"


def test_judge_from_the_document_is_reported(tmp_path):
    for k in (1, 2):
        pm_run(tmp_path, f"r{k}_lc", {"t": 0.9, "c": 0.3}, seed=k)
        rows = [json.loads(x) for x in (tmp_path / f"r{k}_lc" / "judged.jsonl").read_text().splitlines()]
        _write(tmp_path / f"r{k}_lc" / "judged.jsonl", [{k2: v for k2, v in r.items() if k2 != "judge"} for r in rows])
    held = decide(spec(tmp_path))
    assert outcome(held, "readout.same_judge") == "INCONCLUSIVE"
    d = decide(spec(tmp_path, judge_default="composer-2.5"))
    c = next(c for c in d["checks"] if c["check"] == "readout.same_judge")
    assert c["outcome"] == "PASS" and "pre-registration" in c["limitations"][0]


def test_stratified_estimate_matches_hand_computation(tmp_path):
    for k in (1, 2):
        pm_run(tmp_path, f"r{k}_lc", {"t": 0.7, "c": 0.5}, seed=k)
    # q0-q9 are D (treatment found more evidence), q10-q59 the sample; population 200 => wd = 10/200
    st = {f"q{i}": {"sample": i >= 10, "ev": {"t": int(i < 10), "c": 0}} for i in range(N)}
    (tmp_path / "r1_lc" / "strata.json").write_text(json.dumps({"population": 200, "q": st}))
    s = spec(tmp_path, strata_file="strata.json")
    t, c = load_rows(s)
    d = paired_deltas(t, c)
    dd = [d[f"lc:q{i}"] for i in range(10)]
    rr = [d[f"lc:q{i}"] for i in range(10, N)]
    want = 0.05 * sum(dd) / len(dd) + 0.95 * sum(rr) / len(rr)
    assert estimate(s, d)[0] == pytest.approx(want)


def test_beam_scores_only_the_last_good_row_of_a_retried_answer(tmp_path):
    ans, sc = [], []
    for arm in ("t", "c"):
        for b in ("0", "1"):
            for i in range(30):
                aid = f"beam1m|{arm}|{b}|q{i}"
                if arm == "t" and b == "0" and i < 5:
                    ans.append({"id": aid, "answer": "", "error": "Connection refused"})
                ans.append({"id": aid, "answer": f"{arm}{b}{i}", "error": None})
                sc.append({"id": aid, "score": 1.0 if arm == "t" or i % 2 else 0.0, "judge": "sol"})
    _write(tmp_path / "beamprod" / "answers.jsonl", ans)
    _write(tmp_path / "scores.jsonl", sc)
    s = spec(tmp_path, layout="beam", runs=["beamprod"], judged_file=str(tmp_path / "scores.jsonl"),
             model_map={"t": "m1", "c": "m1"})
    d = decide(s)
    assert outcome(d, "readout.errors_excluded") == "PASS", d
    errs = next(c for c in d["checks"] if c["check"] == "readout.errors_excluded")["metrics"]
    assert errs["error_rows"] == 5 and errs["scored_error_rows"] == 0
    assert d["verdict"] == "ALLOW"
    imported = decide(replace(s, answered_elsewhere={"c": "older run"}))
    assert outcome(imported, "readout.same_batch") == "INCONCLUSIVE" and imported["verdict"] == "HOLD"


def test_cli_exit_codes(tmp_path, capsys):
    for k in (1, 2):
        pm_run(tmp_path, f"r{k}_lc", {"t": 0.9, "c": 0.3}, seed=k)
    ok = dict(claim_id="ok", claim="t beats c", claimed_verdict="passed", treatment="t", control="c",
              runs=["r1_lc", "r2_lc"], root=str(tmp_path))
    (tmp_path / "ok.yaml").write_text(yaml.safe_dump(ok))
    (tmp_path / "hold.yaml").write_text(yaml.safe_dump({**ok, "claim_id": "hold", "runs": ["r1_lc"]}))
    (tmp_path / "bad.yaml").write_text(yaml.safe_dump({**ok, "claimed_verdict": "maybe"}))
    assert cli_main(["readout", str(tmp_path / "ok.yaml"), "--out", str(tmp_path / "d.json")]) == 0
    assert json.loads((tmp_path / "d.json").read_text())[0]["verdict"] == "ALLOW"
    assert cli_main(["readout", str(tmp_path / "ok.yaml"), str(tmp_path / "hold.yaml")]) == 3
    assert cli_main(["readout", str(tmp_path / "bad.yaml")]) == 2
    capsys.readouterr()
