# Local simulator — 無料。AWS へのリクエストは発生しない

.PHONY: sim-smoke
sim-smoke:  ## Bell 状態の回路で Docker のローカルシミュレータを動作確認する
	$(LOCAL_RUN) shor-braket smoke --shots "$(SHOTS)"

.PHONY: sim
sim:  ## ローカル参照回路で Shor の位数発見を実行する (既定: N=15, a=7, t=8)
	$(LOCAL_RUN) shor-braket simulate \
		--modulus "$(N)" --base "$(A)" --count-qubits "$(T)" --shots "$(SHOTS)"

.PHONY: sim-all
sim-all:  ## N=15 (主テストケース) と N=6 (縮退ケース) の両方をローカル検証する (回帰用)
	$(MAKE) sim N=15 A=7 T=8 SHOTS="$(SHOTS)"
	$(MAKE) sim N=6 A=5 T=1 SHOTS="$(SHOTS)"

.PHONY: qpu-costs
qpu-costs:  ## 将来の QPU 候補 3 機について指定 shots の 1 task 概算を表示する
	$(LOCAL_RUN) shor-braket qpu-costs --shots "$(SHOTS)"

.PHONY: validated
validated:  ## 検証済みレコード runs/validated の一覧を表示する
	@echo ""
	@ls -1 runs/validated/*.json 2>/dev/null || echo "  (no validated records yet)"
	@echo ""

.PHONY: circuit
circuit:  ## 回路を組み立てて OpenQASM と circuit_hash を表示する (実行はしない)
	$(call notimpl,make circuit,docs/03-execution-gate.md)
