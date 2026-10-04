# Teammate setup: from `git pull` to a working console

What to run on your own machine to get the same system as the build machine (4 Oct): the same 1,011,960 people, the
same 20,000 text features, the same next-best-action queue, and the web console on top. No language model is called
anywhere in steps 1-6. Details behind each step: `requirements.md` (install, data, build), `docs/identity.md` §4.6
(identity verdicts), `ui/README.md` (console), `docs/documentation.md` (change log).

Allow about 30 GB of disk and 16 GB of RAM. Heavy steps run inside a memory-capped systemd scope on Linux (the
`Makefile` does this for you), so an overrun kills only that step, not your session.

---

## 1. Code and packages (once)

```bash
git pull
python -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -e ".[dev,p3,app]"         # backend, models (LightGBM, Feast) and the web console's server
python scripts/setup_router.py         # Ask's local metric matcher, 65 MB, downloaded once
```

You also need **Node.js 20+** (only to build the console once, step 7).

## 2. The data (once)

Download and unzip the release into `maple_data/` exactly as `requirements.md` §3 says (both zips, about 4.7 GB).
Keep the repo outside OneDrive / Dropbox / iCloud, or set `WAREHOUSE_DIR` to a folder outside them.

## 3. Build the warehouse (Layer 1)

```bash
make pipeline                                                     # raw -> curated -> quality -> identity -> gold (~10 min after the first load)
python scripts/second_opinion_manual.py import --labeller luna    # the committed identity verdicts (no model call)
python -m trustissues.factory.identity.build --from relationships # rebuild people with those verdicts
python -m trustissues.factory.pipeline --from gold                # gold, views, publish, identity report
```

The first `make pipeline` also loads the raw files (about 35 min). Check: the last command prints the publish
result with every blocking rule passing, and the identity report says about **1,011,960 people** (Splink is retrained
on each machine, so a handful either way is normal).

## 4. Train the models (once, and after a full data rebuild)

```bash
make train          # training sets, point-in-time features, promise and case models, action values, Feast registry
```

The trained models live in `warehouse/p3/` (not in git), so every machine trains its own.

## 5. The nightly run

```bash
make nightly
```

Stages: pipeline, policy index, **shared text features**, queue, fairness, monitoring. It **does not call any
language model**:

- `shared_text_features` rebuilds the 20,000 hand-labelled text features from `data/review/text_features/` using your
  own warehouse's notes (about 5 s). Git holds only labels, a hash and the position of each evidence sentence, never
  the text. To see how many matched first: `python -m trustissues.features.text_shared import --dry-run`
  (expected: 20,000 identical).
- The `text_features` stage, which sends notes to the configured provider, is skipped unless you ask for it
  (`make nightly NIGHTLY_FLAGS=--with-text-features`). **Do not**: about 265,000 texts are still eligible and each run
  would send 20,000 of them.

After the first full build you can skip the 10-minute pipeline: `python scripts/nightly.py --skip-pipeline`.

## 6. Check it worked

```bash
python -m pytest -q                                              # all suites; ~1,290 pass, some skip
python -m pytest -q tests_integration                            # checks on your published warehouse
TI_UI_INTEGRATION=1 python -m pytest -q tests_ui/test_integration.py   # the console's API on your warehouse
```

What you should see in the warehouse (as of date 2026-09-28):

| Check | Build machine, 4 Oct |
|---|---|
| People (`gold.c360_customer`) | 1,011,960 |
| Text features (`gold.text_features`) | 20,000, of which 11,837 with an evidence quote |
| Recommendations today (`gold.nba_decisions`) | 86,059 |
| Support calls in the queue | 13,522 |
| Decision cards quoting notes or calls | 10,244 |
| Decisions recorded (`gold.nba_overrides`) | 0 (a fresh start) |

## 7. The web console

```bash
make ui-build                                           # once: builds ui/web/dist
python ui/serve.py --demo --shared-access               # asks for one demo password at start-up; http://127.0.0.1:8000
```

Sign in with **any active staff ID** from `curated.agents` and that password, on the tab for the person's role
(Agent, Team lead, Hardship, QA; a wrong tab fails like a wrong password). Staff IDs that make a good test set (one
real team plus a specialist and QA):

| Tab | Staff ID | Who |
|---|---|---|
| Agent | `AG-5944` | agent, vulnerable-customer certified, 271 recommendations |
| Agent | `AG-1785` | senior agent, same team |
| Agent | `AG-8916` | agent, same team, not certified |
| Team lead | `AG-4595` | their team lead |
| Hardship | `AG-1125` | hardship specialist with a queue of their own |
| QA | `AG-3805` | QA analyst |

- Add `--ask-llm` to let Ask use the language model (policy answers, call summaries, generated queries). It needs
  `OPENROUTER_API_KEY` in `.env`. Without it, certified metrics, case facts and refusals still answer.
- The server opens the warehouse only per request, so the nightly run can go while it is up (the console says "Data is
  being refreshed" for a moment). Do not click decisions while a nightly stage is starting.
- Which role sees which screens and customers: `ui/README.md` ("Roles and access"). Decisions you click (accept,
  override, approve, reject, the batch) are written to `gold.nba_overrides` on your machine only. To start clean
  again: `DELETE FROM gold.nba_overrides` with the console stopped.
- Individual accounts with MFA instead of the shared password: `python ui/accounts.py add <staff id>` and
  `enroll-mfa` (`ui/README.md`).

## 8. Please don't

- Run `make nightly NIGHTLY_FLAGS=--with-text-features` or `python -m trustissues.features.text_extract` (sends notes to
  the provider; the team has not agreed a provider for note text).
- Switch on `identity.llm_enabled` in `config/settings.yaml` (sends unmasked identity pairs to a provider; on hold,
  `merge.md` §3).
- Commit anything from `warehouse/` (the warehouse, accounts, models and the Luna working files are gitignored on
  purpose).
- Push reports regenerated after you clicked decisions in the console: `reports/monitoring.md` then counts your test
  clicks as real overrides. Clear `gold.nba_overrides` and rerun `python scripts/nightly.py --only monitoring` first.

## If something goes wrong

| Symptom | Cause and fix |
|---|---|
| `Could not set lock on file ... trustissues.duckdb` | Another process holds the warehouse (a console request, tests, a nightly stage). Stop it and retry. |
| `import --dry-run` reports texts "different" | Your notes differ from the build machine's (another data version). Their labels are kept, their evidence quotes dropped. Re-download the release if many differ. |
| Queue cards show no quotes | `gold.text_features` is empty: run `python scripts/nightly.py --only shared_text_features`, then `--from queue`. |
| Console says "not connected" for a screen | That backend module failed to import: check `pip install -e ".[dev,p3,app]"` ran in this `.venv`. |
| The pipeline is killed partway | It hit the 16 GB cap: close other heavy programs and rerun from the stage it reached (`python -m trustissues.factory.pipeline --from <stage>`). |
