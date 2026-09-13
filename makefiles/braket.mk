# Amazon Braket — ⚠️ 課金が発生する。実行ゲートを必ず経由すること
#
# プロファイル分割:
#   読み取り系 (devices / device-info / task-status) → AWS_PROFILE_RO   (MFA 不要)
#   投入系     (submit-sv1 / submit-qpu)             → AWS_PROFILE_EXEC (MFA 必須)
# 読み取りプロファイルで submit を叩くと IAM が AccessDenied を返す。これは仕様。

BRAKET_REGIONS ?= us-east-1 us-west-1 eu-north-1

# 投入系ターゲットは実行プロファイルを強制する
define require_exec_profile
	@test -n "$(AWS_PROFILE_EXEC)" || { \
		echo ""; \
		echo "  AWS_PROFILE_EXEC is not set. .env に実行用プロファイル名を設定すること。"; \
		echo "  テンプレート: .env.example"; \
		echo ""; \
		exit 1; }
endef

.PHONY: devices
devices:  ## 利用可能な Braket デバイスをリージョン横断で一覧する (無料)
	@for r in $(BRAKET_REGIONS); do \
		echo ""; echo "=== $$r ==="; \
		aws braket search-devices --filters '[]' --region $$r \
			--query 'devices[].[deviceName,deviceType,deviceStatus,deviceArn]' \
			--output table 2>/dev/null || echo "  (unavailable in $$r)"; \
	done

.PHONY: device-info
device-info:  ## 指定デバイスの詳細とコスト構造を表示する (例: make device-info DEVICE=garnet)
	$(call notimpl,make device-info,docs/04-devices-and-cost.md)

.PHONY: submit-sv1
submit-sv1:  ## オンデマンドシミュレータ SV1 に投入する (⚠️ 課金あり・要 validated レコード)
	$(call notimpl,make submit-sv1,docs/03-execution-gate.md)

.PHONY: submit-qpu
submit-qpu:  ## 実機 QPU に投入する (⚠️ 課金あり・要 validated レコード・確認プロンプトあり)
	$(call notimpl,make submit-qpu,docs/03-execution-gate.md)

.PHONY: task-status
task-status:  ## 投入済みタスクの状態を確認する
	$(call notimpl,make task-status,docs/03-execution-gate.md)

.PHONY: report
report:  ## 実行結果を理想分布と比較してレポートを生成する
	$(call notimpl,make report,docs/02-architecture.md)
