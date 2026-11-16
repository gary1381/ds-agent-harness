# Public release notes

This repository is a cleaned copy of the private working repository, published with a fresh git history. The
experiments' pre-registrations record sha256 digests of frozen files. A few of those files were edited for
publication: to remove local paths, to neutralise decision quotes, and to stop with a clear message where a file is
not distributed. The edits do not change any logic that an experiment measured. The digests of the files as they were
frozen are listed here so that the references in the experiment documents can still be checked against them.

## Code and frozen inputs edited for publication

| file | change | sha256 before the change (as frozen) | referenced in |
|---|---|---|---|
| `experiments/E3c/tasks.py` | default data path: a local dataset folder outside the repository → `workspace/external/hillstrom/raw/` (still overridable by `DSH_HILLSTROM`) | `c188c7bc8d914f734c4d260735247cb0fc650cb21944faa1c300d4f4bfa6c820` | `docs/experiments/E3c/DESIGN.md` |
| `experiments/E3c/label_v2.py` | stops with a message when the (undistributed) bank or key is missing | `dcb49374a36d4e226eb10e10e48f810a32faa6e79e9f57eb54467870df4a2142` | `docs/experiments/E3c/PREREGISTRATION.md` |
| `experiments/E3c/run_v2.py` | same | `740762b6f04c52caad3a7df74043e0f047d256b8717798d0cf08f9ee84ca8a23` | `docs/experiments/E3c/PREREGISTRATION.md` |
| `experiments/E3c/review_keys.py` | same | `857f5233f39473099af6ff58232e3ed5dbf1360df62265fdb9c57682172eb31a` | — |
| `experiments/E3c/LABEL_GUIDE_v2.md` | notes that the key is not distributed | `b8914e831ffa5845ff61edbbf55adcd81c53135124357eeb734fff82f6413137` | `docs/experiments/E3c/PREREGISTRATION.md` |
| `experiments/E5/coverage.py` | stops with a message when E3c's bank is missing | `d5111ba6b0d5286357d5b0acb428d077482d702940bebf51db58995375a4524a` | `docs/experiments/E5/PREREGISTRATION.md` |
| `experiments/E3d/pilot.py` | default data path → `workspace/external/nyc-tlc/raw` (`DSH_TLC_RAW`) | `23ea89f51aa58d8aea355dfd697d0c5d0370109df29920bc8ec23e5263a9a113` | `docs/experiments/E3d/PREREGISTRATION.md` |
| `experiments/E3d/run_dbt.py` | default paths → `workspace/external/tlc-indicators/raw/` (`DSH_TLC_INDICATORS`) and `workspace/de_dbt_cache` (`DSH_DE_DBT_CACHE`, was an absolute path on a data disk); one comment | `5d25ec5bb063e347018e87c9ee5aa4c765dcd3f91bee29a0aa7396b591b5ea8c` | `docs/experiments/E3d/PREREGISTRATION.md` |
| `src/dsharness/bench/claude_api.py` | default key file `~/.config/dsharness/env` (was a file shared with another private project); docstring | `b09c9ded3bee94325840a8e82d848fca126ee649f9a095ac069a3f7e2247b62f` | — |
| `src/dsharness/bench/openai_api.py` | same as above | `1fa0256049744d2f184f426e48ebd3a1ce51db3d41e652342588d8ba03b12cac` | `docs/experiments/E3b/DEVIATIONS.md` |
| `src/dsharness/bench/budget.py` | docstring only | `93ce57533cdd40af3f1b6221aa648747fbdc2f9f0e964d3629d969778a9882b2` | `docs/experiments/E3b/DEVIATIONS.md` |
| `src/dsharness/bench/census.py` | two comment lines only | `270975da43c2f1ab800c86b4badbfc0557cd287ca40aabe33a51c307b701d669` | — |
| `experiments/E3b/post_census_v2.sh` | one comment (daily budget) | `e151593212655c5472bc1577db3ddfd9ab0cbb7e5efee7462299a26b37c2c061` | — |
| `tests/test_mcp.py` | the Cursor cloud-agent config test is skipped when `.cursor/mcp.json` is absent | `c5aeae1badb5ebe9989c6bf522a254d9d6be695599b1bbbc2d018edb30ebcf97` | — |

## Documents edited for publication

- `docs/STATUS.md`, `docs/STATUS.zh-CN.md`: private repository name, the internal dashboard, the daily API cap and the
  subscription note removed; the internal to-do table replaced by a list of open research questions. Results and
  numbers unchanged.
- `docs/MODEL_POLICY.md` and the deviation logs (`docs/experiments/E3a/`, `E3b/`, `E3c/`, `E3d/DEVIATIONS.md`): quotes
  from the author's chat messages replaced by neutral statements of each decision ("Decision (date): ..."); the daily
  budget and billing notes removed; local paths replaced by descriptions. The deviation facts are unchanged.
- `docs/experiments/E3d/REPORT.md`, `REPORT.zh-CN.md`, `docs/experiments/E5/DESIGN.md`: the same for one quote each.
- `docs/experiments/E3b/REPORT.md`, `REPORT.zh-CN.md`, `docs/experiments/E5/DESIGN.md`, `PREREGISTRATION.md`: mentions of the
  daily API cap removed; costs unchanged.
- `docs/architecture.svg`: billing notes (subscription, daily cap) replaced by the transport and the spend ledger.
- `docs/experiments/E3c/PREREGISTRATION.md`, `REPORT.md`: a note that the bank and key are not distributed.
- `docs/experiments/E3b/PREREGISTRATION.md`, `docs/experiments/PLAN.md`, `docs/experiments/H_v0.5/review_luna_1.md`:
  three broken links fixed (two pointed into unpublished run folders and are now plain text).
- `docs/experiments/E3d/DESIGN.md`: a local disk path replaced by a description.

`experiments/E1b/harness_v01/` (the frozen v0.1 harness and its `SHA256SUMS`) and the harness patches
(`experiments/*/harness_v0*.patch`) are unchanged.

## Not published

| what | why |
|---|---|
| per-attempt run folders, agent transcripts, sandbox homes, evaluator databases (`experiments/*/runs/`, `workspace/`) | bulky, and transcripts can contain local paths and account metadata; they were never in version control |
| experiment E2 (the gate applied to the author's other projects) | it references private projects; only its summary line appears in STATUS |
| the status dashboard (`experiments/dashboard/`) | internal tool; it reads the unpublished run folders and the API spending ledger |
| Cursor cloud-agent configuration (`.cursor/`) | agent tooling for the author's own setup; not needed to run the harness |
| raw datasets | see [DATA.md](DATA.md): downloaded from the publishers by `scripts/fetch_public_data.py` |
| the E3c request bank and answer key (`experiments/E3c/bank_v2.yaml`, `keys_v2.yaml`) | derived from the Hillstrom file, which has no licence; the E3c reports keep the aggregate results |

## Third-party code

No third-party source code is vendored. Dependencies are installed from PyPI (`pyproject.toml`, `uv.lock`):
duckdb, pandas, pyarrow, numpy, scipy, scikit-learn, pandera, sqlglot, PyYAML and mcp (MIT, BSD or Apache-2.0
licences), pytest (MIT) and hypothesis (MPL-2.0) for tests only, openpyxl (MIT) for the retail example. The E3d dbt projects declare
dbt_utils, dbt_expectations and dbt_date in `packages.yml` (Apache-2.0); they are downloaded by `dbt deps`, not
included. The agents' sandbox environment for E3b–E3d is pinned in `experiments/env_dt/requirements.txt`.
