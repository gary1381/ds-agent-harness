"""E3b v2 labelling and analysis (experiments/E3b/label_v2.py, analyze_v2.py) on synthetic runs only: no model call,
no attempt, no sealed data."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

E3B = Path(__file__).resolve().parents[1] / "experiments/E3b"


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, E3B / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


A = _load("e3b_analyze_v2", "analyze_v2.py")
HELD = [f"t{i}" for i in range(1, 7)]
AGENTS, ARMS, REPS = ("claude", "codex"), ("N", "H"), 2


def make_run(tmp: Path, spec, tasks=HELD, seen=("s1",), mode="sealed", drop=()) -> Path:
    """spec(agent, task, arm, rep) -> (record overrides, label or None, truth or None)."""
    run = tmp / "run"
    (run / "truth").mkdir(parents=True)
    tasks = list(tasks) + list(seen)
    (run / "run.json").write_text(json.dumps({"tasks": tasks, "arms": list(ARMS), "agents": list(AGENTS),
                                              "reps": REPS, "sets": {t: "seen" if t in seen else "heldout"
                                                                     for t in tasks}}))
    labels, lines = {"_meta": {}}, []
    for ag in AGENTS:
        for t in tasks:
            for arm in ARMS:
                for k in range(1, REPS + 1):
                    if (ag, t, arm, k) in drop:
                        continue
                    over, lab, tr = spec(ag, t, arm, k)
                    rec = {"agent": ag, "task": t, "set": "seen" if t in seen else "heldout", "arm": arm, "rep": k,
                           "status": "OK", "seconds": 120.0, "cost_usd": 0.5 if ag == "claude" else None,
                           "tokens": {"input_tokens": 1000, "output_tokens": 100} if ag == "codex" else None,
                           "delivered": True, "answer": "Forecast: about 1,000 orders.", **over}
                    lines.append(json.dumps(rec))
                    if lab is not None:
                        labels[f"{ag}/{t}-{arm}-r{k}"] = lab
                    if tr is not None:
                        (run / "truth" / f"{ag}_{t}-{arm}-r{k}.json").write_text(json.dumps(tr))
    (run / "attempts.jsonl").write_text("\n".join(lines) + "\n")
    (run / "labels.json").write_text(json.dumps(labels))
    (run / "truth" / "summary.json").write_text(json.dumps({"mode": mode, "rows": []}))
    return run


def lab(supported=True, codes=(), one=None, two=None, adjudicated=False):
    s = {"supported": supported, "codes": list(codes), "why": "synthetic"}
    return {**s, "labeller1": one or s, "haiku": two or s, "adjudicated": adjudicated}


def truth(codes=(), ungraded=False, not_typed=0, coverage=()):
    return {"program_codes": list(codes), "ungraded": ungraded, "not_typed": ["1"] * not_typed,
            "claims": [{"coverage": list(coverage)}]}


def clean(ag, t, arm, k):
    return {}, lab(), truth()


# --- outcomes -------------------------------------------------------------------------------------------------------

def test_outcome_combines_label_and_program():
    r = {"delivered": True, "answer": "x"}
    assert A.outcome(r, {"supported": True}, set()) == "supported-delivery"
    assert A.outcome(r, {"supported": True}, {"E-gross"}) == "silent-error"       # program code overrides the label
    assert A.outcome(r, {"supported": False}, set()) == "silent-error"
    assert A.outcome(r, {"supported": True}, set(), extra_bad=True) == "silent-error"
    held = {"delivered": False, "answer": "x"}
    assert A.outcome(held, {"supported": True}, set()) == "false-hold"
    assert A.outcome(held, {"supported": True}, {"E-overclaim"}) == "caught"
    assert A.outcome({"delivered": False, "answer": "  \n"}, {}, set()) == "no-answer"
    assert A.outcome({"delivered": True, "answer": "x"}, {}, set()) == "unlabelled"
    assert A.outcome({"missing": True}, {}, set()) == "missing"


def test_error_classes():
    assert A.error_class({"codes": ["E-calc", "E-read"]}, {"E-gross"}) == "visible"
    assert A.error_class({"codes": ["E-read"]}, {"E-gross"}) == "targeted"
    assert A.error_class({"codes": []}, {"E-overclaim"}) == "targeted"
    assert A.error_class({"codes": ["E-overclaim"]}, set()) == "blind"            # a label E-overclaim is semantic
    assert A.error_class({"codes": ["E-causal", "E-nodata"]}, set()) == "blind"


def test_quantile_type7():
    assert A.quantile([1, 2, 3, 4], 0.5) == 2.5
    assert A.quantile([0, 10], 0.025) == pytest.approx(0.25)
    assert A.quantile([5], 0.975) == 5


# --- completeness ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("case", ["missing", "unlabelled", "no_truth", "rehearsal"])
def test_refuses_unless_complete(tmp_path, case):
    def spec(ag, t, arm, k):
        first = (ag, t, arm, k) == ("claude", "t1", "N", 1)
        return {}, (None if case == "unlabelled" and first else lab()), \
            (None if case == "no_truth" and first else truth())
    run = make_run(tmp_path, spec, mode="rehearse" if case == "rehearsal" else "sealed",
                   drop={("claude", "t1", "N", 1)} if case == "missing" else ())
    with pytest.raises(SystemExit, match="not final"):
        A.analyse(run, interim=False)
    res = A.analyse(run, interim=True)
    assert res["status"]["interim"]
    if case == "missing":
        assert res["status"]["missing"] == 1 and res["outcomes"]["claude/t1-N-r1"] == "missing"
    if case == "unlabelled":
        assert res["status"]["unlabelled"] == 1
    if case == "no_truth":
        assert res["status"]["no_truth"] == 1


def test_no_answer_needs_no_label_or_truth(tmp_path):
    def spec(ag, t, arm, k):
        if (ag, t, arm, k) == ("codex", "t2", "H", 2):
            return {"answer": None, "delivered": False}, None, None
        return clean(ag, t, arm, k)
    res = A.analyse(make_run(tmp_path, spec), interim=False)
    assert res["status"]["no_answer"] == 1
    h = res["sets"]["heldout"]["by_arm"]["H"]
    assert h["R1"] == 0 and h["R1_noanswer_as_error"] == pytest.approx(1 / 24)
    assert h["R1_replies_only"] == 0


# --- metrics and rules ----------------------------------------------------------------------------------------------

def test_harness_met(tmp_path):
    """N: every reply a silent error; H: every reply a supported delivery."""
    def spec(ag, t, arm, k):
        if arm == "N":
            return {}, lab(False, ["E-read"]), truth()
        return {}, lab(), truth()
    res = A.analyse(make_run(tmp_path, spec), interim=False)
    ho = res["sets"]["heldout"]
    assert ho["by_arm"]["N"]["n"] == 6 * 2 * REPS
    assert ho["by_arm"]["N"]["R1"] == 1 and ho["by_arm"]["N"]["R1_blind"] == 1
    assert ho["by_arm"]["H"]["R1"] == 0 and ho["by_arm"]["H"]["R2"] == 0
    assert ho["R1_diff"]["diff"] == -1 and ho["R1_diff"]["ci"] == [-1, -1] and ho["R1_diff"]["draws"] == A.BOOT
    assert res["rules"]["R1"] == "met" and res["rules"]["R2 too strict (R2 > 15%)"] is False
    assert ho["by_arm"]["H"]["pass_k"] == 1 and ho["by_arm"]["N"]["pass_k"] == 0
    assert ho["by_arm"]["H"]["cells"] == 12
    assert "seen" in res["sets"] and "s1 N" in res["sets"]["seen"]["by_task"]


def test_false_holds_block_the_rule(tmp_path):
    def spec(ag, t, arm, k):
        if arm == "N":
            return {}, lab(False, ["E-causal"]), truth()
        return {"delivered": False}, lab(), truth()
    res = A.analyse(make_run(tmp_path, spec), interim=False)
    h = res["sets"]["heldout"]["by_arm"]["H"]
    assert h["R1"] == 0 and h["R2"] == 1
    assert res["rules"]["R1"] == "not met: fewer silent errors, but R2 above 15%"
    assert res["rules"]["R2 too strict (R2 > 15%)"] is True


def test_harness_worse_and_split(tmp_path):
    """H delivers program errors (targeted) and wrong past figures (visible); N is clean."""
    def spec(ag, t, arm, k):
        if arm == "H" and k == 1:
            return {}, lab(), truth(["E-gross"])
        if arm == "H":
            return {}, lab(False, ["E-calc"]), truth(["E-overclaim"])
        return clean(ag, t, arm, k)
    res = A.analyse(make_run(tmp_path, spec), interim=False)
    ho = res["sets"]["heldout"]
    h = ho["by_arm"]["H"]
    assert h["R1"] == 1 and h["R1_targeted"] == 0.5 and h["R1_visible"] == 0.5 and h["R1_blind"] == 0
    assert h["E_gross"] == 0.5 and h["E_overclaim_typed"] == 0.5
    assert h["R2"] is None and h["R2_draws"] == 0                 # no clean reply in H: R2 undefined, not 0
    assert res["rules"]["R1"].startswith("harness worse")
    assert ho["codes"]["H"]["silent-error"] == {"E-gross": 12, "E-calc": 12, "E-overclaim": 12}


def test_ungraded_sensitivity(tmp_path):
    """Ungraded replies are judged by their labels in the primary analysis and as errors in the sensitivity."""
    def spec(ag, t, arm, k):
        return {}, lab(), truth(ungraded=(arm == "N"))
    res = A.analyse(make_run(tmp_path, spec), interim=False)
    ho = res["sets"]["heldout"]
    assert ho["by_arm"]["N"]["R1"] == 0 and ho["by_arm"]["N"]["R1_ungraded_as_error"] == 1
    assert res["rules"]["R1"].startswith("inconclusive")
    assert res["rules"]["R1, ungraded counted as errors (sensitivity)"] == "met"
    assert ho["typing"]["N"]["ungraded"] == 24 and ho["typing"]["H"]["ungraded"] == 0


def test_coverage_and_typing(tmp_path):
    def spec(ag, t, arm, k):
        cov = [{"key": None, "level": 0.9, "hit": True}, {"key": "2025-09", "level": 0.9, "hit": False},
               {"key": None, "level": None, "hit": True}] if arm == "H" else []
        return {}, lab(), truth(not_typed=3 if arm == "N" else 0, coverage=cov)
    res = A.analyse(make_run(tmp_path, spec), interim=False)
    ho = res["sets"]["heldout"]
    assert ho["coverage"]["H"] == {"0.9": {"n": 48, "hit": 0.5}, "unstated": {"n": 24, "hit": 1.0}}
    assert ho["coverage"]["N"] == {}
    assert ho["typing"]["N"]["not_typed_per_reply"] == 3 and ho["typing"]["N"]["replies_with_any"] == 1
    assert ho["typing"]["H"]["not_typed_per_reply"] == 0


def test_cost_per_supported(tmp_path):
    res = A.analyse(make_run(tmp_path, clean), interim=False)
    n = res["sets"]["heldout"]["by_arm"]["N"]
    assert n["usd_per_supported"] == pytest.approx(0.5)            # Claude: dollars only
    assert n["tokens_per_supported"] == pytest.approx(1100)        # Codex: tokens only, never added to dollars
    assert n["median_min"] == 2


def test_kappa_by_agent_and_arm(tmp_path):
    def spec(ag, t, arm, k):
        if ag == "codex" and arm == "H" and k == 1:                 # labellers disagree; adjudicated to supported
            return {}, lab(one={"supported": True, "codes": []}, two={"supported": False, "codes": ["E-read"]},
                           adjudicated=True), truth()
        if t in ("t1", "t2") and k == 2:                            # some agreed errors so kappa is defined
            return {}, lab(False, ["E-read"]), truth()
        return clean(ag, t, arm, k)
    res = A.analyse(make_run(tmp_path, spec), interim=False)
    ag = res["agreement"]
    assert ag["claude N"]["agreement"] == 1 and ag["claude N"]["kappa"] == pytest.approx(1)
    assert ag["codex H"]["n"] == 14 and ag["codex H"]["agreement"] == pytest.approx(7 / 14)
    assert ag["adjudicated"] == 6 + 1                               # six held-out tasks plus the seen one
    assert set(ag) >= {"all", "N", "H", "claude", "codex", "claude H", "codex N"}


def test_bootstrap_is_deterministic_and_reported(tmp_path):
    def spec(ag, t, arm, k):
        bad = t != "s1" and (int(t[1:]) + k + (arm == "H")) % 3 == 0
        return {}, lab(not bad, [] if not bad else ["E-nodata"]), truth()
    run = make_run(tmp_path, spec)
    one, two = A.analyse(run, interim=False), A.analyse(run, interim=False)
    assert one["sets"]["heldout"]["R1_diff"] == two["sets"]["heldout"]["R1_diff"]
    x = one["sets"]["heldout"]["by_arm"]["N"]
    assert x["R1_ci"][0] <= x["R1"] <= x["R1_ci"][1]
    md = A.report(one)
    assert "R1(H) − R1(N), paired over requests" in md and "Decision rules" in md and "C, range coverage" in md


def test_main_writes_results(tmp_path, monkeypatch):
    run = make_run(tmp_path, clean)
    out = tmp_path / "out"
    monkeypatch.setattr("sys.argv", ["analyze_v2.py", str(run), "--out", str(out)])
    assert A.main() == 0
    res = json.loads((out / "results.json").read_text())
    assert res["status"]["interim"] is False and res["rules"]["R1"].startswith("inconclusive")
    assert (out / "results.md").read_text().startswith("# E3b v2 results\n")


# --- blind sheets ---------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def L():
    return _load("e3b_label_v2", "label_v2.py")


def test_sheet_is_blind(L, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / "top.csv").write_text("buyer_id,p\n" + "\n".join(f"{i},0.{i}" for i in range(1, 30)) + "\n")
    r = {"agent": "codex", "task": "rp90", "arm": "H", "rep": 1, "delivered": True, "gate_final": "ALLOW",
         "gates": ["HOLD", "ALLOW"], "claims": {"claims": [{"id": "SECRET_ID", "type": "list", "file": "top.csv",
                                                            "says": "SECRET_SAYS"}]},
         "answer": "The ranked list is in top.csv."}
    text = L.sheet(r, "S001", ["- past_x (total): 12"], work)
    for leak in ("codex", "SECRET_ID", "SECRET_SAYS", "ALLOW", "HOLD", "delivered", "gate", "claims.json"):
        assert leak not in text, leak
    assert "Which of our customers are likely to buy" in text and "Supported:" in text and "Unsupported:" in text
    assert "- past_x (total): 12" in text
    assert "columns: buyer_id, p" in text and "\n9,0.9" in text and "10,0.10" in text and "11,0.11" not in text
    assert L.file_view(work, "absent.csv").endswith("(missing)")


def test_check_rows(L):
    want = {"S001", "S002"}
    ok = [{"sheet": "S001", "supported": True, "codes": []},
          {"sheet": "S002", "supported": False, "codes": ["E-causal"], "why": "q"}]
    assert L.check_rows(ok, want, "t")["S002"]["codes"] == ["E-causal"]
    with pytest.raises(AssertionError):
        L.check_rows([{**ok[0], "codes": ["E-read"]}, ok[1]], want, "t")       # codes on a supported label
    with pytest.raises(AssertionError):
        L.check_rows([ok[0], {**ok[1], "codes": ["E-scale"]}], want, "t")      # an E3c code, not E3b's
    with pytest.raises(SystemExit, match="without a label"):
        L.check_rows(ok[:1], want, "t")
    with pytest.raises(SystemExit, match="twice"):
        L.check_rows(ok + ok[:1], want, "t")


def test_truth_past_figure_is_program_e_calc():
    T = _load("e3b_truth_v2", "truth_v2.py")
    c = {"id": "p1", "type": "total", "window": ["2025-06-01", "2025-09-01"], "values": [{"key": None, "point": 1234.0}]}
    ok = T.grade_claim(c, {"values": [{"actual": 1236.0}]}, None, {}, {"claims": {}})
    bad = T.grade_claim(c, {"values": [{"actual": 1250.0}]}, None, {}, {"claims": {}})
    assert ok["errors"] == [] and bad["errors"] == ["E-calc"]
    fut = dict(c, window=["2025-09-01", "2025-12-01"])
    assert not T.is_past(fut, T.ASOF)
    assert A.error_class({"codes": []}, {"E-calc"}) == "visible"


def test_undefined_r2_never_meets():
    d = {"ci": [-0.2, -0.05]}
    assert A.verdict({"R1": 0.3}, {"R1": 0.1, "R2": None}, d).startswith("inconclusive")
    assert A.verdict({"R1": 0.3}, {"R1": 0.1, "R2": 0.05}, d) == "met"


def test_per_buyer_bands_give_coverage(tmp_path):
    T = _load("e3b_truth_v2", "truth_v2.py")
    (tmp_path / "acc.csv").write_text("buyer_id,p,lo,hi\n1,100,90,110\n2,50,40,60\n")
    c = {"id": "b", "type": "per_buyer", "file": "acc.csv", "id_column": "buyer_id", "point_column": "p",
         "low_column": "lo", "high_column": "hi", "level": 0.8}
    real = {"values": [{"key": "1", "actual": 105.0}, {"key": "2", "actual": 70.0}, {"key": "3", "actual": 5.0}]}
    g = T.grade_claim(c, real, None, {}, {"files": tmp_path, "claims": {}})
    assert [x["hit"] for x in g["coverage"]] == [True, False, False]
    assert A.coverage([{"coverage": g["coverage"]}])["0.8 (per-buyer bands)"]["n"] == 3
