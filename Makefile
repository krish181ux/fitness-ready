.PHONY: help load inventory serve test probe pull load-api stack clean

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-12s %s\n", $$1, $$2}'

load:      ## Load the real account export in raw/export into wearable-real.duckdb
	python -m loader.load_garmin raw/export --db wearable-real.duckdb

inventory: ## List export files and their field mapping
	python -m loader.inventory raw/export

serve:     ## Run the tools server on :8000 (WEARABLE_DB overrides the database)
	python -m tools.server

test:      ## Run the suite (builds its own synthetic data in a temp dir)
	python -m pytest

probe:     ## Dump one day of raw Garmin API responses to raw/api_samples/
	python -m fetcher.probe --date $(or $(DATE),$(shell date +%F))

pull:      ## Pull the last 30 days (or START=/END=) from Garmin Connect into raw/api/
	python -m fetcher.pull $(if $(START),--start $(START)) $(if $(END),--end $(END))

load-api:  ## Pull, then load raw/api/ into wearable-real.duckdb
	$(MAKE) pull
	python -m loader.load_garmin raw/api --db wearable-real.duckdb

stack:     ## n8n + Langfuse + LiteLLM + tools in Docker
	docker compose up -d

clean:     ## Drop the database (raw data and the journal are untouched)
	rm -f wearable-real.duckdb wearable-real.duckdb.wal
