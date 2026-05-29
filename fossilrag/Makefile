.PHONY: install lint fmt test test-unit up up-aws up-ui down logs ps seed smoke demo config e2e ui-install ui-test ui-build clean

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

up-ui:  ## build + start postgres + api + the React UI (http://localhost:5173)
	docker compose --profile ui up -d --build

down:  ## stop the stack (all profiles) and drop volumes
	docker compose --profile aws --profile ui down -v

logs:  ## tail the API logs
	docker compose logs -f api

ps:  ## show running services (aws profile included)
	docker compose --profile aws ps

seed:  ## ingest the sample documents through the running stack
	python -m scripts.seed

smoke:  ## end-to-end HTTP smoke test against a running API (retrieval half)
	python -m scripts.smoke

loadtest:  ## concurrent load test on /excavate → latency percentiles + throughput
	python -m scripts.loadtest $(API_URL)

demo:  ## run the S3->SQS->worker->silver ingestion demo (needs up-aws)
	docker compose --profile aws run --rm worker python -m scripts.demo_aws

config:  ## validate compose for the default + aws + ui profiles (no creds)
	docker compose config -q \
	  && docker compose --profile aws config -q \
	  && docker compose --profile ui config -q \
	  && echo "compose OK (default + aws + ui)"

ui-install:  ## install the UI deps (Bun)
	cd ui && bun install --frozen-lockfile

ui-test:  ## UI lint + typecheck + render tests
	cd ui && bun run lint && bun run typecheck && bun run test

ui-build:  ## production build of the UI
	cd ui && bun run build

e2e: up  ## full local end-to-end: up -> smoke -> down
	python -m scripts.smoke
	docker compose down -v

clean:  ## remove caches
	rm -rf .pytest_cache .ruff_cache **/__pycache__ .coverage
