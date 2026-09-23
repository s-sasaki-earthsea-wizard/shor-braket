# Local simulator / emulator — 無料。AWS へのリクエストは発生しない

.PHONY: sim-smoke
sim-smoke:  ## Bell 状態の回路で Docker のローカルシミュレータを動作確認する
	$(LOCAL_RUN) shor-braket smoke --shots "$(SHOTS)"

.PHONY: sim
sim:  ## Shor の位数発見を実行し、SVG/PNG/HTML の教材を生成する (既定: N=15, a=7, t=8)
	$(LOCAL_RUN) shor-braket simulate \
		--modulus "$(N)" --base "$(A)" --count-qubits "$(T)" --shots "$(SHOTS)"

.PHONY: sim-all
sim-all:  ## N=15 (主テストケース) と N=6 (縮退ケース) の両方をローカル検証する (回帰用)
	$(MAKE) sim N=15 A=7 T=8 SHOTS="$(SHOTS)"
	$(MAKE) sim N=6 A=5 T=1 SHOTS="$(SHOTS)"

.PHONY: emulate
emulate:  ## 校正スナップショットから LocalEmulator を組み、ネイティブ回路を検証・実行する (無料・オフライン。例: make emulate DEVICE=garnet)
	$(LOCAL_RUN) shor-braket emulate --device "$(DEVICE)" --shots "$(SHOTS)"

.PHONY: emulate-all
emulate-all:  ## 3 機すべてで emulate を実行し、比較図と report.md を生成する
	$(LOCAL_RUN) shor-braket emulate --device all --shots "$(SHOTS)"

N15_DEVICE ?= all
N15_ORACLE ?= all
N15_SHOTS  ?= 20000
# Count register size for the N=15 circuits. 2 is the default and the route-check circuit;
# 3 adds one count qubit whose U^4 = I control leaves it idling in |+> (an in-situ T2 probe).
N15_T      ?= 2

.PHONY: emulate-n15
emulate-n15:  ## N=15 の QPU 互換回路 (swap network, t=2) を 3 機の LocalEmulator で実行し信号の残存を比較する (無料・オフライン。N15_DEVICE / N15_ORACLE / N15_SHOTS)
	$(LOCAL_RUN) shor-braket emulate-n15 --device "$(N15_DEVICE)" --oracle "$(N15_ORACLE)" --shots "$(N15_SHOTS)" \
		--count-qubits "$(N15_T)"

.PHONY: validate-n15
validate-n15:  ## N=15 の QPU 互換回路をエミュレートし、合格した構成に validated レコードを発行する (無料・オフライン)
	$(LOCAL_RUN) shor-braket emulate-n15 --device "$(N15_DEVICE)" --oracle "$(N15_ORACLE)" \
		--shots "$(N15_SHOTS)" --count-qubits "$(N15_T)" --issue-records

N15I_SHOTS ?= 4000

.PHONY: emulate-n15-iterative
emulate-n15-iterative:  ## N=15 の反復 QPE (feed-forward, count 1 qubit) を標準 QPE と並べて LocalEmulator で比較し、TVD の標本床も出す (無料・オフライン。N15_DEVICE / N15_ORACLE / N15I_SHOTS)
	$(LOCAL_RUN) shor-braket emulate-n15-iterative --device "$(N15_DEVICE)" --oracle "$(N15_ORACLE)" --shots "$(N15I_SHOTS)"

.PHONY: qpu-costs
qpu-costs:  ## 将来の QPU 候補 3 機について指定 shots の 1 task 概算を表示する
	$(LOCAL_RUN) shor-braket qpu-costs --shots "$(SHOTS)"

.PHONY: validated
validated:  ## 検証済みレコード runs/validated の一覧を表示する (無料・オフライン)
	$(LOCAL_RUN) shor-braket records

.PHONY: circuit
circuit:  ## QPU 互換回路を組み立てて circuit_hash を表示する (実行はしない。DEVICE / ORACLE / QASM=1)
	$(LOCAL_RUN) shor-braket circuit --device "$(DEVICE)" --oracle "$(ORACLE)" \
		$(if $(QASM),--qasm,--no-qasm)

.PHONY: decoherence-study
decoherence-study:  ## 待機 qubit の T1/T2 と非対称読み出しを足した予測を今のモデルと並べる (無料・オフライン。DEVICE / ORACLE / N15_T)
	$(LOCAL_RUN) shor-braket decoherence-study --device "$(DEVICE)" --oracle "$(ORACLE)" \
		--count-qubits "$(N15_T)"
