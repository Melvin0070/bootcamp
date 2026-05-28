.PHONY: install lint fmt test test-unit up down logs seed smoke e2e clean

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

up:  ## build + start the local stack (postgres + api)
	docker compose up -d --build

down:  ## stop the stack and drop volumes
	docker compose down -v

logs:  ## tail the API logs
	docker compose logs -f api

seed:  ## ingest the sample documents through the running stack
	python -m scripts.seed

smoke:  ## end-to-end HTTP smoke test against a running API
	python -m scripts.smoke

e2e: up  ## full local end-to-end: up -> smoke -> down
	python -m scripts.smoke
	docker compose down -v

clean:  ## remove caches
	rm -rf .pytest_cache .ruff_cache **/__pycache__ .coverage
