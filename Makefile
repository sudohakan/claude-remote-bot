.PHONY: install test lint format clean run

install:
	pip install -e ".[dev]" 2>/dev/null || pip install -e .

install-dev:
	pip install -e . && pip install pytest pytest-asyncio pytest-cov pytest-mock black isort flake8 mypy

test:
	pytest tests/ -v --tb=short

test-cov:
	pytest tests/ -v --cov=src --cov-report=html --cov-report=term-missing

lint:
	flake8 src/ tests/
	mypy src/

format:
	black src/ tests/
	isort src/ tests/

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; \
	find . -name "*.pyc" -delete 2>/dev/null; \
	rm -rf .pytest_cache htmlcov .coverage; \
	echo "Cleaned."

run:
	python -m src.main

db-init:
	python -c "import asyncio; from src.storage.database import DatabaseManager; asyncio.run(DatabaseManager('sqlite:///data/bot.db').initialize())"
