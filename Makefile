.PHONY: help fixture load load-real inventory inventory-real serve test evals probe pull load-api eval-store eval-summary eval-export stack clean

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-12s %s\n", $$1, $$2}'

fixture:   ## Generate the synthetic Garmin export in raw/fixture
	python -m loader.make_fixture --out raw/fixture

inventory: ## Phase 0: list fixture files and their field mapping
	python -m loader.inventory raw/fixture

inventory-real: ## Phase 0 against the real account export in raw/export
	python -m loader.inventory raw/export

load:      ## Phase 1: build wearable.duckdb from the fixture (the eval database)
	python -m loader.load_garmin raw/fixture
	python -m tools.journal --seed-fixture

load-real: ## Load the real account export into wearable-real.duckdb (kept separate on purpose)
	python -m loader.load_garmin raw/export --db wearable-real.duckdb

serve:     ## Phase 2: run the tools server on :8000 (WEARABLE_DB overrides the database)
	python -m tools.server

test:      ## Run the suite
	python -m pytest

evals:     ## Phase 4: recompute bucket A ground truth into evals/dataset.csv
	python -m evals.ground_truth --write

probe:     ## Stage 1: dump one day of raw Garmin API responses to raw/api_samples/
	python -m fetcher.probe --date $(or $(DATE),$(shell date +%F))

pull:      ## Stage 2: pull the last 30 days (or START=/END=) from Garmin Connect into raw/api/
	python -m fetcher.pull $(if $(START),--start $(START)) $(if $(END),--end $(END))

load-api:  ## Pull, then load raw/api/ into wearable-real.duckdb (never the fixture database)
	$(MAKE) pull
	python -m loader.load_garmin raw/api --db wearable-real.duckdb

eval-store: ## Phase 5: pull finished eval runs out of n8n into evals.duckdb
	python -m evals.store collect --workflow-id $(WORKFLOW) $(foreach l,$(LABELS),--label $(l))

eval-export: ## Write the eval store to committed CSVs in evals/results/
	python -m evals.store export

eval-summary: ## One row per stored eval run
	python -m evals.store summary

stack:     ## Phase 3+: n8n + Langfuse + tools in Docker
	docker compose up -d

clean:     ## Drop the databases (raw data is untouched)
	rm -f wearable.duckdb wearable.duckdb.wal wearable-real.duckdb wearable-real.duckdb.wal
	@echo 'evals.duckdb kept: it holds run history, not derived data. rm it by hand if you mean to.'
