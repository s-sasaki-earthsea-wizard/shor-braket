# Lint, type check, test

.PHONY: lint
lint:  ## Docker 内の ruff で静的解析する
	$(LOCAL_RUN) ruff check src tests

.PHONY: format
format:  ## Docker 内の ruff で整形する
	$(LOCAL_RUN) ruff check --fix src tests
	$(LOCAL_RUN) ruff format src tests

.PHONY: typecheck
typecheck:  ## Docker 内の mypy で型検査する
	$(LOCAL_RUN) mypy src

.PHONY: test
test:  ## Docker 内で pytest を実行する (aws マーカーは既定で除外)
	$(LOCAL_RUN) pytest -o cache_dir=/tmp/pytest-cache

.PHONY: test-cov
test-cov:  ## Docker 内でカバレッジ付き pytest を実行する
	$(LOCAL_RUN) pytest -o cache_dir=/tmp/pytest-cache \
		--cov=shor_braket --cov-report=term-missing \
		--cov-report=html:runs/coverage

.PHONY: check
check: lint typecheck test  ## lint + typecheck + test をまとめて実行する
