.DEFAULT_GOAL := help
.PHONY: help install dev test cov lint fmt type check serve ui docker clean eval

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

install: ## Install the package
	uv pip install -e .

dev: ## Install with dev and UI extras
	uv pip install -e ".[dev,ui]"

test: ## Run the test suite
	pytest

cov: ## Run tests with a coverage report
	pytest --cov --cov-report=term-missing --cov-report=html

lint: ## Lint
	ruff check .

fmt: ## Format
	ruff format .

type: ## Type-check (strict)
	mypy

check: lint type test ## Everything CI runs
	ruff format --check .

serve: ## Run the API with auto-reload
	resume-parser serve --reload

ui: ## Run the Streamlit interface
	resume-parser ui

docker: ## Build the container image
	docker build -t resume-ai-parser:latest .

eval: ## Run the evaluation harness (needs real credentials)
	python evals/run_eval.py

clean: ## Remove caches and build artefacts
	rm -rf build dist *.egg-info .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
