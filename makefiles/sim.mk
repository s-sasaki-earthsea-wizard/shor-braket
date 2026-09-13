# Local simulator — 無料。AWS へのリクエストは発生しない

.PHONY: sim
sim:  ## ローカルシミュレータで位数発見を実行し検証する (例: make sim N=15)
	$(call notimpl,make sim,docs/02-architecture.md / Phase 1-2)

.PHONY: sim-all
sim-all:  ## N=15 (主テストケース) と N=6 (縮退ケース) の両方をローカル検証する (回帰用)
	$(call notimpl,make sim-all,docs/01-why-n6-is-degenerate.md)

.PHONY: validated
validated:  ## 検証済みレコード runs/validated の一覧を表示する
	@echo ""
	@ls -1 runs/validated/*.json 2>/dev/null || echo "  (no validated records yet)"
	@echo ""

.PHONY: circuit
circuit:  ## 回路を組み立てて OpenQASM と circuit_hash を表示する (実行はしない)
	$(call notimpl,make circuit,docs/03-execution-gate.md)
