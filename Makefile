# Common runs. Each target says what it does; see docs/p3_plan.md (run order) and requirements.md (setup).
PY ?= .venv/bin/python
# Heavy runs go in their own memory-capped systemd scope, so an overrun kills only that run, not the desktop session
# (config/settings.yaml `duckdb:` caps DuckDB itself). Empty where systemd-run is missing. Override: make pipeline CAP=
CAP ?= $(shell command -v systemd-run >/dev/null 2>&1 && echo systemd-run --user --scope --quiet -p MemoryMax=16G -p MemorySwapMax=2G)

.PHONY: help pipeline features train queue fairness monitoring nightly app test

help:
	@echo "make pipeline    Layer 1: raw -> curated -> quality (factory/pipeline.py)"
	@echo "make features    P3 training sets and point-in-time features (promise and case)"
	@echo "make train       features, the incumbent baseline, both models, action values, Feast registry"
	@echo "make queue       today's NBA queue into gold.nba_decisions (stop the app first)"
	@echo "make fairness    reports/fairness_report.md"
	@echo "make monitoring  reports/monitoring.md"
	@echo "make nightly     pipeline, queue, fairness, monitoring (scripts/nightly.py)"
	@echo "make app         the Streamlit app (Agent queue, Decision card)"
	@echo "make test        the test suite"

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
	$(CAP) $(PY) scripts/nightly.py

app:
	.venv/bin/streamlit run app/Home.py

test:
	$(PY) -m pytest tests -q
