.PHONY: install dev test lint run-interactive run-api clean help

PYTHON := python3
PIP := pip3

help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install the package
	$(PIP) install -e .

dev: ## Install with development dependencies
	$(PIP) install -e ".[dev]"

test: ## Run the test suite
	$(PYTHON) -m pytest tests/ -v --tb=short

test-coverage: ## Run tests with coverage report
	$(PYTHON) -m pytest tests/ -v --cov=k8s_agent --cov-report=term-missing

lint: ## Run linting checks
	$(PYTHON) -m ruff check src/ tests/
	$(PYTHON) -m ruff format --check src/ tests/

format: ## Auto-format code
	$(PYTHON) -m ruff format src/ tests/

run-interactive: ## Launch the interactive CLI
	$(PYTHON) -m k8s_agent.cli.main interactive

run-api: ## Start the API server
	$(PYTHON) -m k8s_agent.cli.main serve

run-scan: ## Run a cluster scan
	$(PYTHON) -m k8s_agent.cli.main scan

clean: ## Clean build artifacts
	rm -rf build/ dist/ *.egg-info src/*.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
