.PHONY: install check test test-docker

install:
	uv venv
	uv pip install -e ".[dev]"

check:
	uv run ruff check src tests
	uv run mypy

# the default run needs no docker daemon; the docker suite is its own target
test:
	uv run pytest -m "not docker"

test-docker:
	uv run pytest -m docker
