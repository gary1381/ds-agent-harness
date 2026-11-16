"""Download the public datasets the real-data experiments (E3a-E3d, E4) and examples/online_retail use.

No dataset is redistributed in this repository. This script fetches each one from its publisher into
`workspace/external/` (git-ignored), where the experiment scripts look for it by default, and checks the
files against the sha256 sums recorded when the experiments ran (`scripts/checksums/`). Publishers can
re-issue files; a checksum mismatch is reported, not hidden. See docs/DATA.md for sources and terms.

    uv run --group external python scripts/fetch_public_data.py retail          # UCI Online Retail II (CC BY 4.0)
    uv run python scripts/fetch_public_data.py tlc                              # NYC TLC trip files (~0.6 GB)
    uv run python scripts/fetch_public_data.py tlc --months 2024-12 2025-01     # a subset
    uv run python scripts/fetch_public_data.py indicators                       # NYC TLC industry indicators
    uv run python scripts/fetch_public_data.py hillstrom --research-use-only    # no licence: research use only

Stdlib only, except `retail`, which needs pandas + openpyxl to convert the xlsx to parquet.
"""
from __future__ import annotations

import argparse
import hashlib
import io
from pathlib import Path
import shutil
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "workspace/external"
SUMS = Path(__file__).resolve().parent / "checksums"
UA = {"User-Agent": "ds-harness (public dataset fetch)"}

UCI_ZIP = "https://archive.ics.uci.edu/static/public/502/online+retail+ii.zip"
TLC_BASE = "https://d37ci6vzurychx.cloudfront.net"
TLC_YELLOW = ["2020-08", "2020-09", "2020-10", "2022-10", "2022-11", "2022-12", "2023-01", "2023-02", "2023-08",
              "2023-09", "2024-12", "2025-01", "2026-09"]
TLC_GREEN = ["2023-01", "2023-02"]
INDICATORS = "https://data.cityofnewyork.us/api/views/v6kb-cqej/rows.csv?accessType=DOWNLOAD"
HILLSTROM = ("http://www.minethatdata.com/"
             "Kevin_Hillstrom_MineThatData_E-MailAnalytics_DataMiningChallenge_2008.03.20.csv")


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def expected(sums: str) -> dict[str, str]:
    lines = (SUMS / sums).read_text().splitlines()
    return {name: digest for digest, name in (l.split() for l in lines if l.strip())}


def download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        print(f"exists  {dest.relative_to(ROOT)}")
        return dest
    print(f"fetch   {url}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r, tmp.open("wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
    tmp.rename(dest)
    return dest


def verify(p: Path, want: str | None) -> bool:
    got = sha256(p)
    if want is None:
        print(f"sha256  {p.name} {got} (no recorded sum)")
        return True
    ok = got == want
    print(f"{'ok     ' if ok else 'MISMATCH'} {p.name}" + ("" if ok else f": got {got}, recorded {want}"))
    return ok


def install_sums(sums: str, folder: Path) -> None:
    """The experiment scripts read SHA256SUMS next to the raw/ folder."""
    shutil.copyfile(SUMS / sums, folder / "SHA256SUMS")


def retail(_: argparse.Namespace) -> bool:
    import pandas as pd   # needs the `external` dependency group (openpyxl)

    out = EXT / "online_retail_ii/online_retail_ii.parquet"
    if out.exists():
        print(f"exists  {out.relative_to(ROOT)}")
        return True
    z = download(UCI_ZIP, EXT / "online_retail_ii/online_retail_ii.zip")
    with zipfile.ZipFile(z) as zf:
        name = next(n for n in zf.namelist() if n.lower().endswith(".xlsx"))
        sheets = pd.read_excel(io.BytesIO(zf.read(name)), sheet_name=None)
    df = pd.concat(sheets.values(), ignore_index=True)
    df["Invoice"] = df["Invoice"].astype(str)
    df["StockCode"] = df["StockCode"].astype(str)
    df["Customer ID"] = df["Customer ID"].astype("float64")
    df.to_parquet(out, index=False)
    print(f"wrote   {out.relative_to(ROOT)}: {len(df):,} rows (the experiments used 1,067,371)")
    return len(df) == 1_067_371


def tlc(a: argparse.Namespace) -> bool:
    want = expected("nyc-tlc.sha256")
    raw = EXT / "nyc-tlc/raw"
    files = [f"yellow_tripdata_{m}.parquet" for m in (a.months or TLC_YELLOW)]
    if not a.months:
        files += [f"green_tripdata_{m}.parquet" for m in TLC_GREEN]
    ok = True
    for f in files:
        ok &= verify(download(f"{TLC_BASE}/trip-data/{f}", raw / f), want.get(f))
    ok &= verify(download(f"{TLC_BASE}/misc/taxi_zone_lookup.csv", raw / "taxi_zone_lookup.csv"),
                 want.get("taxi_zone_lookup.csv"))
    install_sums("nyc-tlc.sha256", raw.parent)
    return ok


def indicators(_: argparse.Namespace) -> bool:
    raw = EXT / "tlc-indicators/raw"
    p = download(INDICATORS, raw / "tlc_industry_indicators.csv")
    install_sums("tlc-indicators.sha256", raw.parent)
    # This file is updated monthly by its publisher, so a later download will not match the recorded sum.
    return verify(p, expected("tlc-indicators.sha256").get(p.name))


def hillstrom(a: argparse.Namespace) -> bool:
    if not a.research_use_only:
        sys.exit("The Hillstrom file has no licence from its author. Pass --research-use-only to confirm that you "
                 "will use it for research only and not redistribute it.")
    raw = EXT / "hillstrom/raw"
    p = download(HILLSTROM, raw / "hillstrom_2008-03-20.csv")
    install_sums("hillstrom.sha256", raw.parent)
    return verify(p, expected("hillstrom.sha256").get(p.name))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="what", required=True)
    sub.add_parser("retail").set_defaults(fn=retail)
    t = sub.add_parser("tlc")
    t.add_argument("--months", nargs="*", help="yellow-taxi months, YYYY-MM (default: all the experiments used)")
    t.set_defaults(fn=tlc)
    sub.add_parser("indicators").set_defaults(fn=indicators)
    h = sub.add_parser("hillstrom")
    h.add_argument("--research-use-only", action="store_true")
    h.set_defaults(fn=hillstrom)
    a = ap.parse_args(argv)
    return 0 if a.fn(a) else 1


if __name__ == "__main__":
    raise SystemExit(main())
