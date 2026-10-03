# Requirements and Setup: build the warehouse on your own machine

This gets you from nothing to a populated `warehouse/trustissues.duckdb` (raw and curated schemas) using only the original dataset. Nothing here depends on another person's database file: every teammate builds their own from the source data, and `scripts/verify_warehouse.py` proves it came out the same.

For what the code does and why, see [`docs/documentation.md`](docs/documentation.md) and [`docs/curation.md`](docs/curation.md).

## 1. What you need

| | Requirement | Notes |
|---|---|---|
| Python | 3.10 or newer | Developed and tested on 3.14.6 |
| OS | Windows, macOS or Linux | Developed on Windows 11. Paths in the code are OS-neutral |
| Disk | **about 30 GB free** | Both zips 4.7 GB + extracted data about 9.5 GB + warehouse (raw 5.7 GB, curated adds roughly the same again, see [curation.md](docs/curation.md) for the measured size) |
| RAM | **16 GB recommended** | Built on a 15.7 GB machine. The raw load peaked around 1.8 GB for the Python process and DuckDB spills to disk when it needs more. 8 GB should work but is untested |
| Network | one-off ~4.7 GB download | Both zips: the release (2.2 GB) and the voice pack (about 2.5 GB, 2,000 WAV files). The voice pack is needed by the plan (recordings are checksum-verified) but **nothing in the current raw/curated build reads the audio**, so the build works without it for now |
| Packages | see [`requirements.txt`](requirements.txt) | `duckdb`, `pyyaml`, `openpyxl`, `pytest`, `huggingface_hub` |

**Do not put the repo inside a OneDrive / Dropbox / iCloud synced folder** if you can avoid it. The data folder is about 7 GB and the database 10 GB or more; the sync client competes with the build for disk and makes it noticeably slower. If you must, set `WAREHOUSE_DIR` to a folder outside the synced area (section 5).

## 2. Install

From the repository root:

```bash
python -m venv .venv
# Windows:      .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate
pip install -e ".[dev]"
```

`pip install -e` installs the `trustissues` package from `src/` so `python -m trustissues...` works from anywhere. `pip install -r requirements.txt` also works for the packages alone, but then you must set `PYTHONPATH=src`.

## 3. Get the data

The dataset is on Hugging Face: `nuxsh/maple-collections-hackathon`. It must end up at `maple_data/maple_collections_release/` (this is the default in `config/settings.yaml`).

```bash
pip install -U huggingface_hub
hf download nuxsh/maple-collections-hackathon --repo-type dataset --local-dir maple_data
```

The command must be **on one line** with the folder name directly after `--local-dir`. It downloads two zips: `maple_collections_release.zip` (required) and `maple_collections_voice.zip` (see the voice step below). If it reports `Aborted!` before finishing, run the same command again: it resumes.

Then unzip the release **inside `maple_data/`**:

| OS | Command |
|---|---|
| Windows (cmd or PowerShell) | `cd maple_data && tar -xf maple_collections_release.zip` |
| macOS / Linux | `cd maple_data && unzip maple_collections_release.zip` |

Check you have `maple_data/maple_collections_release/data/customers.csv` and `maple_data/maple_collections_release/schema/schema.json`.

**Voice pack (per the implementation plan).** Unzip `maple_collections_voice.zip` the same way, then move its `files/voice/` folder into `maple_data/maple_collections_release/files/` so `voice_samples.file_path` resolves. The plan's checksum check of the recordings is not built yet; until it is, skipping this step does not affect the build or `verify_warehouse.py`.

**Windows: "`hf` is not recognized".** pip installed `hf.exe` into a folder that is not on PATH. Either call it by full path, `"%APPDATA%\Python\Python3XX\Scripts\hf.exe" download ...` (use your Python version), or add that Scripts folder to your PATH.

**Alternative:** download the zips from the dataset's *Files and versions* tab on huggingface.co and unzip them as above.

## 4. Build

One command runs ingest, dictionary, curate and labels, and prints a timing for each stage:

```bash
python -m trustissues.factory.pipeline
```

Or stage by stage:

```bash
python -m trustissues.factory.ingest.load_raw        # source files -> raw.*     (about 35 min on the reference machine)
python -m trustissues.factory.ingest.dictionary      # data dictionary -> raw._dictionary + config/sensitivity.yaml
python -m trustissues.factory.curate.build_curated   # raw.* -> curated.*        (see docs/curation.md for timing)
python -m trustissues.factory.ingest.dictionary      # again, so derived std_* columns get sensitivity labels
```

Useful options: `python -m trustissues.factory.pipeline --from curate` (resume), `--only ingest`, `load_raw --only customers agents` (a subset).

The build is **re-runnable**. Running ingest again reads only new or changed files and skips unchanged ones, so a second run takes seconds.

## 5. Verify

```bash
python scripts/verify_warehouse.py     # exit code 0 = every check passed
python -m pytest tests -q              # unit tests, a few seconds, no dataset needed
```

`verify_warehouse.py` checks that every raw table has exactly the row count the release publishes, that all transcript files loaded, that there was no blocking schema drift, and that curated row counts reconcile (raw minus removed duplicates). If your counts differ, your download or unzip was incomplete.

## 6. Where things go, and how to change it

| | Default | Override |
|---|---|---|
| Source data | `maple_data/maple_collections_release/` | env `MAPLE_DATA_DIR` |
| Warehouse | `warehouse/trustissues.duckdb` | env `WAREHOUSE_DIR` (a folder) |
| Settings | `config/settings.yaml` | edit the file |

```bash
# example: keep the 10+ GB database outside a synced folder
export WAREHOUSE_DIR=/data/trustissues          # Windows: set WAREHOUSE_DIR=D:\trustissues
```

`maple_data/` and `warehouse/` are gitignored. **Never commit them.**

## 7. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `database is locked` / IO error opening the warehouse | DuckDB allows **one writer process at a time**. Close any other Python session, notebook or DuckDB shell that has the file open, and never run two builds together. Read-only access is fine when nothing is writing |
| `FileNotFoundError ... schema.json` | The data is not at the expected path. Re-check section 3, or set `MAPLE_DATA_DIR` |
| `ModuleNotFoundError: trustissues` | Run `pip install -e ".[dev]"` from the repo root, in the same environment you run Python from |
| Ingest stops with `registered columns missing` | Schema drift protection: a column the schema expects is not in a file. With the official dataset this means a damaged or partial unzip. Re-extract the zip |
| A step looks stuck for minutes with no output | The large tables (`transactions`, `loan_instalments`, `card_statements`, `contact_history`) take several minutes each, and the transcript-file step loads 25,000 small files. Each source prints one line when it finishes |
| Build was interrupted | Just run it again. Ingest skips what is already loaded. Curate rebuilds a table from raw each time |
| `unzip` not found on Windows | Use `tar -xf` as in section 3 |
| pip reports `huggingface-hub` conflicts with `transformers` / `sentence-transformers` | Those packages need `huggingface_hub<2`. If you use them in the same environment, install `huggingface_hub<2` instead. Untested here |

## 8. What each person should end up with

After section 5 passes, every teammate has the same `raw.*` (as received, text) and `curated.*` (typed, deduplicated, with standardised identity columns). Later layers (identity, gold, features, Ask, decisioning) are built on top of these, so a teammate who can pass `verify_warehouse.py` can run all downstream code locally.

**One database or several?** The implementation plan says the team shares one DuckDB at the start. Two ways to do that, both fine:
- **Everyone builds their own** (this document). Reproducible, nothing to transfer, and it proves the pipeline works from the original data. Costs about an hour of unattended time.
- **Copy a built file** (`warehouse/trustissues.duckdb`, 10 GB or more) from whoever has finished. It is gitignored, so it must be transferred outside git. After copying, run `python scripts/verify_warehouse.py` on it. Remember DuckDB allows one writer at a time, so teammates should work on their **own copy** and not on one shared file.

Until `curated.*` reaches a stage you need (for example `curated.promises_to_pay`, which P3 is waiting on), check `curated._build_log` to see which tables exist.
