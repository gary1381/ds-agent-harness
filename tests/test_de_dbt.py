"""Pure parts of the dbt pipeline CI (the sandboxed builds are exercised by the E3d experiment, not here)."""
from pathlib import Path

import duckdb

from dsharness.tools import de_dbt as D


def spec(tmp_path, **kw):
    return D.PipelineSpec(project=tmp_path, raw_dir=tmp_path, served={}, drift="2026-09", env=tmp_path, **kw)


def test_paths_outside_agent_area_are_refused():
    assert D.check_paths({"models/staging/stg_trips.sql": "", "contracts/yellow_trips.yml": "", "tests/x.sql": ""}) == []
    bad = D.check_paths({"DEFINITIONS.md": "", "ci/build.py": "", "dbt_project.yml": "", "models/../ci/x.py": "",
                         "/etc/passwd": ""})
    assert len(bad) == 5


def test_differences_to_the_cent():
    want = {"trips": "10", "fare_amount": "100.00", "cbd_congestion_fee": ""}
    assert D.differences({"trips": "10", "fare_amount": "100.01", "cbd_congestion_fee": ""}, want, list(want)) == []
    assert D.differences({"trips": "10", "fare_amount": "100.02", "cbd_congestion_fee": ""}, want, list(want)) \
        == ["fare_amount: 100.02 vs 100.00"]
    assert D.differences(None, want, list(want)) == ["no metrics row"]
    month = {"month": "2025-01", "trips": "10"}
    assert D.differences(dict(month), month, list(month)) == []
    assert D.differences({**month, "month": "2025-02"}, month, list(month)) == ["month: '2025-02' vs '2025-01'"]


def test_manifest_diff_names_weakened_tests():
    t = lambda **c: {"config": {k: c.get(k) for k in D.TEST_CONFIG_KEYS}, "kwargs": None, "checksum": c.get("sum", "a")}  # noqa: E731
    base = {"test.taxi.reconcile": t(severity="ERROR"), "test.taxi.rows_in_month": t(severity="ERROR"),
            "test.taxi.not_null_x": t(severity="ERROR")}
    cand = {"test.taxi.reconcile": t(severity="warn"), "test.taxi.rows_in_month": t(severity="ERROR", sum="b"),
            "test.taxi.new": t()}
    d = D.manifest_diff(base, cand)
    assert d["removed"] == ["not_null_x"] and d["added"] == ["new"]
    assert d["changed"]["reconcile"] == ["severity: 'ERROR' -> 'warn'"]
    assert d["changed"]["rows_in_month"] == ["SQL edited"]


def test_control_total_holds_a_month_off_by_more_than_the_tolerance(tmp_path):
    csv = tmp_path / "ind.csv"
    csv.write_text("Year/Month,License Class,Trips Per Day,Farebox Per Day\n2026-09-01,Yellow,100,1000\n"
                   "2026-09-01,Green,5,50\n")
    s = spec(tmp_path, control_csv=csv)
    ok = D.control_total(s, {"trips": str(101 * 30), "total_amount": str(1100 * 30), "tip_amount": str(90 * 30)})
    assert ok["outcome"] == "PASS"
    off = D.control_total(s, {"trips": str(112 * 30), "total_amount": str(1200 * 30), "tip_amount": str(100 * 30)})
    assert off["outcome"] == "FAIL" and set(off["reason"].split("{")[1].split("}")[0].split(", ")) >= {"'trips_per_day': 0.12"}


def test_conservation_compares_served_columns_with_the_export(tmp_path):
    f = tmp_path / "yellow_tripdata_2026-09.parquet"
    duckdb.sql("SELECT TIMESTAMP '2026-09-03 10:00' AS tpep_pickup_datetime, 10.0 AS fare_amount, 12.5 AS total_amount "
               "UNION ALL SELECT TIMESTAMP '2026-09-04 10:00', 5.0, 6.0 "
               "UNION ALL SELECT TIMESTAMP '2026-08-31 23:00', 7.0, 8.0").write_parquet(str(f))
    s = spec(tmp_path)
    assert D.conservation(s, {"trips": "2", "fare_amount": "15.00", "total_amount": "18.50"})["outcome"] == "PASS"
    dropped = D.conservation(s, {"trips": "1", "fare_amount": "10.00", "total_amount": "12.50"})
    assert dropped["outcome"] == "FAIL" and "trips: 1 vs 2" in dropped["reason"]


def test_every_defect_keeps_the_export_schema(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    duckdb.sql("SELECT 2::INTEGER AS VendorID, TIMESTAMP '2026-09-03' AS tpep_pickup_datetime, 1.0::DOUBLE AS fare_amount, "
               "0.5::DOUBLE AS extra, 2.5::DOUBLE AS congestion_surcharge FROM range(400)").write_parquet(str(raw / "yellow_tripdata_2025-01.parquet"))
    s = D.PipelineSpec(project=tmp_path, raw_dir=raw, served={"2025-01": Path("x")}, drift="2026-09", env=tmp_path,
                       cache=tmp_path / "cache")
    want = duckdb.sql(f"DESCRIBE SELECT * FROM '{raw}/yellow_tripdata_2025-01.parquet'").fetchall()
    for d in D.DEFECTS:
        out = D.defect_raw(s, "2025-01", d) / "yellow_tripdata_2025-01.parquet"
        assert duckdb.sql(f"DESCRIBE SELECT * FROM '{out}'").fetchall() == want, d
