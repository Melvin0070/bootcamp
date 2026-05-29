.PHONY: install lint fmt test test-unit up up-aws down logs ps seed smoke demo config e2e clean

install:  ## Install the package + dev tooling (editable)
	pip install -e ".[dev]"

lint:  ## ruff lint + format check
	ruff check .
	ruff format --check .

fmt:  ## auto-format + autofix
	ruff format .
	ruff check --fix .

test:  ## full suite with coverage (needs DATABASE_URL for integration)
	pytest -v --cov=fossilrag --cov-report=term-missing

test-unit:  ## unit tests only (no DB)
	pytest -v -m "not integration"

up:  ## build + start the lean spine (postgres + api) — what CI smoke-tests
	docker compose up -d --build

up-aws:  ## build + start the full emulated stack (+ localstack + bootstrap + worker)
	docker compose --profile aws up -d --build

down:  ## stop the stack (both profiles) and drop volumes
	docker compose --profile aws down -v

logs:  ## tail the API logs
	docker compose logs -f api

ps:  ## show running services (aws profile included)
	docker compose --profile aws ps

seed:  ## ingest the sample documents through the running stack
	python -m scripts.seed

smoke:  ## end-to-end HTTP smoke test against a running API (retrieval half)
	python -m scripts.smoke

demo:  ## run the S3->SQS->worker->silver ingestion demo (needs up-aws)
	docker compose --profile aws run --rm worker python -m scripts.demo_aws

config:  ## validate compose for BOTH the default and aws profiles (no creds)
	docker compose config -q && docker compose --profile aws config -q && echo "compose OK (default + aws)"

e2e: up  ## full local end-to-end: up -> smoke -> down
	python -m scripts.smoke
	docker compose down -v

clean:  ## remove caches
	rm -rf .pytest_cache .ruff_cache **/__pycache__ .coverage
