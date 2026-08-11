.PHONY: install lint test fixtures image integration

install:
	uv sync --extra dev

lint:
	uv run ruff check src tests
	uv run ruff format --check src tests

test:
	uv run pytest -v

image:
	docker build -t fwupd-webui:dev .

fixtures:
	./scripts/capture-fixtures.sh

integration:
	./scripts/integration-test.sh
