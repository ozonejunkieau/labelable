# Labelable development commands

# Default recipe - show available commands
default:
    @just --list

# Install dependencies
install:
    uv sync --all-extras

# Run the development server
run:
    uv run uvicorn labelable.app:app --reload --host 0.0.0.0 --port 7979

# Run tests
test:
    uv run pytest

# Run tests with coverage
test-cov:
    uv run pytest --cov=labelable --cov-report=term-missing

# Lint code
lint:
    uv run ruff check src tests

# Format code
fmt:
    uv run ruff format src tests
    uv run ruff check --fix src tests

# Type check (if mypy is added later)
# typecheck:
#     uv run mypy src

# Build docker image
build:
    docker build -t labelable:local .

# Build Home Assistant add-on (specify arch: amd64, aarch64, armv7)
build-addon arch="amd64":
    docker build -t labelable-addon:local \
        -f ha-addon/Dockerfile \
        --build-arg BUILD_FROM=ghcr.io/home-assistant/{{arch}}-base-python:3.13-alpine3.21 \
        .

# Run local docker image
docker-run:
    docker run --rm -p 7979:7979 \
        -v $(pwd)/config.yaml:/app/config.yaml \
        -v $(pwd)/templates:/app/templates \
        labelable:local

# Clean up
clean:
    rm -rf .pytest_cache .ruff_cache __pycache__ .coverage
    find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
