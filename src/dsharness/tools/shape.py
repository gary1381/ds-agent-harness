"""What answer shape a question asks for (H v0.2, docs/experiments/H_v0.2_PLAN.md F1/F3).

The v0.1 gate checked that the submitted rows equal the governed rows of the declared call and that
the call names the question's metric, months and filters. It never asked whether those rows are the
answer the question wants: monthly fen rows reconciled perfectly for "what was Q1 net revenue in
CNY?", and were VERIFIED. This module reads the question for the parts of the shape that decide
that: one value over several months or one row per month, CNY or fen, and Chinese month scopes.

Like `claim_contradictions`, it parses only the question (never the agent's claims), and every rule
is one-sided: a parse it is unsure of yields no constraint here, and a contradiction costs a human
review (HOLD), never a REJECT.
"""
from __future__ import annotations

import re

_ZH_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
           "十一": 11, "十二": 12}
_ZH_MONTH = r"(1[0-2]|0?[1-9]|十[一二]?|[一二三四五六七八九])"
_SEP = r"\s*(?:至|到|—|–|-|~|～)\s*"


def _n(s: str) -> int:
    return int(s) if s.isdigit() else _ZH_NUM[s]


def _ym(y: int, m: int) -> str:
    return f"{y:04d}-{m:02d}"


def zh_months(text: str) -> list[str]:
    """Months named by Chinese date expressions: 2026年3月, 2026年1月至3月, 2026年1-3月,
    2025年12月至2026年2月, 2026年第一季度 / 一季度 / Q1, 2026年上半年 / 下半年, 2025年全年 / 2025年.
    A range that runs backwards yields '!'. Returns [] when nothing Chinese is found."""
    return zh_scan(text)[0]


def zh_scan(text: str) -> tuple[list[str], str]:
    """(months named by Chinese date expressions, the text with those expressions blanked out)."""
    found: list[str] = []
    s = text

    def span(y0: int, m0: int, y1: int, m1: int) -> list[str]:
        a, b = y0 * 12 + m0 - 1, y1 * 12 + m1 - 1
        return [_ym(k // 12, k % 12 + 1) for k in range(a, b + 1)] if a <= b else ["!"]

    def take(pattern: str, fn) -> None:
        nonlocal s
        for mt in list(re.finditer(pattern, s)):
            found.extend(fn(mt))
        s = re.sub(pattern, " ", s)

    take(rf"(\d{{4}})\s*年\s*{_ZH_MONTH}\s*月(?:份)?{_SEP}(\d{{4}})\s*年\s*{_ZH_MONTH}\s*月",
         lambda mt: span(int(mt[1]), _n(mt[2]), int(mt[3]), _n(mt[4])))
    take(rf"(\d{{4}})\s*年\s*{_ZH_MONTH}\s*(?:月(?:份)?)?{_SEP}{_ZH_MONTH}\s*月",
         lambda mt: span(int(mt[1]), _n(mt[2]), int(mt[1]), _n(mt[3])))
    take(r"(\d{4})\s*年\s*(?:第\s*)?([1-4一二三四])\s*季度",
         lambda mt: span(int(mt[1]), 3 * _n(mt[2]) - 2, int(mt[1]), 3 * _n(mt[2])))
    take(r"(\d{4})\s*年\s*[Qq]\s*([1-4])", lambda mt: span(int(mt[1]), 3 * int(mt[2]) - 2, int(mt[1]), 3 * int(mt[2])))
    take(r"(\d{4})\s*年\s*上半年", lambda mt: span(int(mt[1]), 1, int(mt[1]), 6))
    take(r"(\d{4})\s*年\s*下半年", lambda mt: span(int(mt[1]), 7, int(mt[1]), 12))
    take(rf"(\d{{4}})\s*年\s*{_ZH_MONTH}\s*月", lambda mt: [_ym(int(mt[1]), _n(mt[2]))])
    take(r"(\d{4})\s*年(?:\s*全年|度)?(?![0-9一二三四五六七八九十上下第Qq])", lambda mt: span(int(mt[1]), 1, int(mt[1]), 12))
    return found, s


# "monthly churn rate in 2025" asks for the series; "average monthly", "month-over-month", "月均" do not
_PER_MONTH_EN = re.compile(r"\b(?:each|every|per)\s+(?:\w+\s+)?month\b|\bby\s+month\b|\bmonth[- ]by[- ]month\b|"
                           r"\bfor\s+each\s+of\s+the\s+(?:\w+\s+)?months\b|\bmonthly\b|\bin\s+each\s+of\b", re.I)
_PER_MONTH_ZH = re.compile(r"每(?:个)?月(?!均)|各月|逐月|按月|分月|每一个月")
_NOT_PER_MONTH = re.compile(r"\b(?:average|mean|avg)\s+(?:per\s+month|monthly)\b|\bmonth[- ]over[- ]month\b|"
                            r"平均每(?:个)?月|月均|环比", re.I)


def asks_per_month(question: str) -> bool:
    q = _NOT_PER_MONTH.sub(" ", question)
    return bool(_PER_MONTH_EN.search(q) or _PER_MONTH_ZH.search(q))


_MONTHLY_MEAN = re.compile(r"\b(?:average|mean|avg)\s+(?:per\s+month|monthly)\b|\bmonthly\s+(?:average|mean)\b|"
                           r"\bper\s+month\s+on\s+average\b|月均|平均每(?:个)?月|每月平均", re.I)
_TO_DATE = re.compile(r"\bcumulative\b|\brunning total\b|\byear[- ]to[- ]date\b|\bytd\b|\bto date\b|\bso far\b|"
                      r"累计|截至|截止", re.I)
_PERIOD_TOTAL = re.compile(r"\b(?:total|combined|altogether|in\s+all|cumulative)\b|合计|总共|累计|总计|一共", re.I)


def asks_monthly_mean(question: str) -> bool:
    return bool(_MONTHLY_MEAN.search(question))


def asked_unit(question: str) -> str | None:
    """'cny' if the question asks for CNY / yuan / 元, 'fen' if for fen, None if it names neither or both."""
    cny = bool(re.search(r"\b(?:CNY|RMB|yuan)\b|人民币|元", question, re.I))
    fen = bool(re.search(r"\bfen\b|以分(?:为单位|计)|（分）|\(分\)", question, re.I))
    return "cny" if cny and not fen else "fen" if fen and not cny else None


def shape_contradictions(question: str, call: dict, derive: dict | None, metric_unit: str | None,
                         q_months: tuple[str, str] | None) -> list[str]:
    """Where the question's own words fix the answer's shape, the submission must have it.

    `call` is the declared metric_call, `derive` the normalised derivation (or None), `q_months` the
    months the question names (first, last) if they could be read."""
    out: list[str] = []
    d = derive or {"months": None, "across": None, "unit": None}
    collapsed = d["months"] not in (None, "cumsum", "change") or (d.get("across") or {}).get("dimension") == "month"
    several = q_months is not None and q_months[0] != q_months[1]
    if several and not asks_per_month(question) and not collapsed and "month" not in (call.get("dimensions") or []):
        # one value (per group) over several months was asked; monthly rows are not that answer
        out.append(f"the question asks for one value over {q_months[0]}..{q_months[1]}, but the answer has one row "
                   f"per month; declare derive.months (sum, mean, pool, total or growth) and submit the derived rows")
    if d["months"] not in (None, "cumsum", "change") and asks_per_month(question):
        out.append("the question asks for one row per month, but derive.months collapses the months")
    if d["months"] in ("sum", "pool", "total") and asks_monthly_mean(question):
        out.append(f"the question asks for an average per month, but derive.months is {d['months']!r}; use 'mean'")
    if d["months"] == "mean" and _PERIOD_TOTAL.search(question) and not asks_monthly_mean(question):
        out.append("the question asks for a total over the period, but derive.months is 'mean'")
    out += _span_contradictions(question, call, d, q_months)
    unit = asked_unit(question)
    if metric_unit == "fen":
        if unit == "cny" and d["unit"] != "cny":
            out.append("the question asks for CNY but the rows are in fen; declare derive.unit = 'cny'")
        if unit == "fen" and d["unit"] == "cny":
            out.append("the question asks for fen but derive.unit converts to CNY")
    return out


def _span_contradictions(question: str, call: dict, d: dict, q_months: tuple[str, str] | None) -> list[str]:
    """A step that combines the months (a total, average, pooled rate, growth, a month's share or the top month)
    must combine exactly the months the question names. The claim check alone allows a wider call (the asked rows
    are still in a per-month answer), but a Q1 total declared over December to March is another number (found by
    the v0.5 blind review). A running total must start where the question starts; growth windows must be the
    question's periods. Only a cleanly read scope is used: a list such as "January and February 2026" reads as
    February alone, and equality with an under-read scope would contradict right answers (E1b C-H39)."""
    from .derive import shift_month
    cm = tuple(call.get("months") or ()) or None
    q = tuple(q_months) if q_months else None
    if not (q and cm):
        return []
    op, across, w = d["months"], d.get("across") or {}, d.get("windows")
    if w:
        if q not in ((w[0][0], w[1][1]), tuple(w[1])):
            return [f"the question names {q[0]}..{q[1]}, but derive.windows compare {w[0][0]}..{w[0][1]} with "
                    f"{w[1][0]}..{w[1][1]}"]
        return []
    if cm == q:
        return []
    if op == "growth" and q[0] == q[1] and cm in ((shift_month(q[1], -1), q[1]), (shift_month(q[1], -12), q[1])):
        return []           # one month against the month before or the same month a year earlier
    if op == "sum" and _TO_DATE.search(question):
        return []           # "cumulative revenue at the end of May": a sum from an earlier start (E1b C-H32)
    if op in ("sum", "mean", "pool", "total", "growth") or (
            op is None and across.get("dimension") == "month" and across.get("op") in ("top", "share")):
        return [f"the step combines every month of metric_call.months {cm[0]}..{cm[1]}, but the question names "
                f"{q[0]}..{q[1]}; declare exactly those months"]
    if op == "cumsum" and cm[0] != q[0]:
        return [f"a running total starts at metric_call.months[0] {cm[0]}, but the question starts at {q[0]}"]
    return []
