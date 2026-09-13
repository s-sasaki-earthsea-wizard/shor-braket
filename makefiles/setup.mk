# Environment setup

.PHONY: setup
setup:  ## 依存関係をインストールする (uv sync)
	uv sync --all-groups

.PHONY: lock
lock:  ## 依存関係のロックファイルを更新する
	uv lock --upgrade

.PHONY: clean
clean:  ## キャッシュと一時生成物を削除する (runs/validated は消さない)
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

.PHONY: clean-runs
clean-runs:  ## 生の測定結果 runs/raw を削除する (validated レコードは残す)
	rm -rf runs/raw
	mkdir -p runs/raw
