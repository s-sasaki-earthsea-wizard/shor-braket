# Environment setup

.PHONY: setup
setup:  ## requirements.txt から Docker のローカル開発環境を構築する
	$(COMPOSE) build local

.PHONY: clean
clean:  ## キャッシュと一時生成物を削除する (runs/validated は消さない)
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov runs/coverage .coverage
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

.PHONY: clean-runs
clean-runs:  ## 生の測定結果 runs/raw を削除する (validated レコードは残す)
	rm -rf runs/raw
	mkdir -p runs/raw
