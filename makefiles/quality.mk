# Lint, type check, test

.PHONY: lint
lint:  ## ruff で静的解析する
	uv run ruff check src tests

.PHONY: format
format:  ## ruff で整形する
	uv run ruff format src tests
	uv run ruff check --fix src tests

.PHONY: typecheck
typecheck:  ## mypy で型検査する
	uv run mypy src

.PHONY: test
test:  ## pytest を実行する (aws マーカーは既定で除外)
	uv run pytest

.PHONY: test-cov
test-cov:  ## カバレッジ付きで pytest を実行する
	uv run pytest --cov=shor_braket --cov-report=term-missing --cov-report=html

.PHONY: check
check: lint typecheck test  ## lint + typecheck + test をまとめて実行する
