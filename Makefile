# Common runs. Each target says what it does; see docs/p3_plan.md (run order) and requirements.md (setup).
PY ?= .venv/bin/python
# Heavy runs go in their own memory-capped systemd scope, so an overrun kills only that run, not the desktop session
# (config/settings.yaml `duckdb:` caps DuckDB itself). Empty where systemd-run is missing. Override: make pipeline CAP=
CAP ?= $(shell command -v systemd-run >/dev/null 2>&1 && echo systemd-run --user --scope --quiet -p MemoryMax=16G -p MemorySwapMax=2G)

.PHONY: help pipeline features train queue fairness monitoring nightly ui-build app test

help:
	@echo "make pipeline    Layer 1: raw -> curated -> quality (factory/pipeline.py)"
	@echo "make features    P3 training sets and point-in-time features (promise and case)"
	@echo "make train       features, the incumbent baseline, both models, action values, Feast registry"
	@echo "make queue       today's NBA queue into gold.nba_decisions"
	@echo "make fairness    reports/fairness_report.md"
	@echo "make monitoring  reports/monitoring.md"
	@echo "make nightly     pipeline, policy index, shared text features, queue, fairness, monitoring (scripts/nightly.py;"
	@echo "                 no language model unless NIGHTLY_FLAGS=--with-text-features)"
	@echo "make ui-build    build the web app (ui/web -> ui/web/dist; needs Node 20+)"
	@echo "make app         the web console: FastAPI + React on http://127.0.0.1:8000 (ui/README.md); APP_FLAGS=--demo"
	@echo "make test        the test suite (tests/ and the UI's tests_ui/)"

pipeline:
	$(CAP) $(PY) -m trustissues.factory.pipeline

features:
	$(PY) -m trustissues.features.build_training_set
	$(PY) -m trustissues.features.compute
	$(PY) -m trustissues.features.build_case_training_set
	$(PY) -m trustissues.features.compute --case

train: features
	$(PY) -m trustissues.nba.evaluate
	$(PY) -m trustissues.nba.train
	$(PY) -m trustissues.nba.train --case
	$(PY) -m trustissues.nba.actions
	cd feature_repo && ../.venv/bin/feast apply

queue:
	$(PY) -m trustissues.nba.queue --write-gold

fairness:
	$(PY) -m trustissues.nba.fairness

monitoring:
	$(PY) -m trustissues.governance.monitoring

nightly:
	$(CAP) $(PY) scripts/nightly.py $(NIGHTLY_FLAGS)

ui-build:
	cd ui/web && npm ci && npm run build

# Sign-in accounts first: python ui/accounts.py add <staff id> (ui/README.md). Add --ask-llm to let Ask use the model.
APP_FLAGS ?= --demo
app:
	@test -f ui/web/dist/index.html || $(MAKE) ui-build
	$(PY) ui/serve.py $(APP_FLAGS)

test:
	$(PY) -m pytest tests tests_ui -q
