"""Verification checks. Each returns a result (outcome PASS|FAIL|INCONCLUSIVE|ERROR plus details).

None of them records evidence. The tools layer is the single place that turns check results
into evidence records, so a check can be reused by CI, tools and the benchmark without
side effects.
"""
