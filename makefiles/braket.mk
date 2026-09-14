# Amazon Braket — ⚠️ 課金が発生する。実行ゲートを必ず経由すること
#
# プロファイル分割:
#   読み取り系 (devices / device-snapshot / task-status) → AWS_PROFILE_RO (MFA 不要)
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

# Approved QPU ARNs. src/shor_braket/cost.py is the source of truth; snapshot-import
# rejects a payload whose ARN does not match the key, so drift here fails loudly.
SNAPSHOT_DEVICES ?= garnet emerald ibex
SNAPSHOT_REGION  ?= eu-north-1
SNAPSHOT_ARN_garnet  := arn:aws:braket:eu-north-1::device/qpu/iqm/Garnet
SNAPSHOT_ARN_emerald := arn:aws:braket:eu-north-1::device/qpu/iqm/Emerald
SNAPSHOT_ARN_ibex    := arn:aws:braket:eu-north-1::device/qpu/aqt/Ibex-Q1

define require_ro_profile
	@test -n "$(AWS_PROFILE_RO)" || { \
		echo ""; \
		echo "  AWS_PROFILE_RO is not set. .env に読み取り専用プロファイル名を設定すること。"; \
		echo "  テンプレート: .env.example"; \
		echo ""; \
		exit 1; }
endef

.PHONY: device-snapshot
device-snapshot:  ## 3 機の校正データを GetDevice で取得し devices/snapshots へ保存する (読み取りのみ・課金なし)
	$(require_ro_profile)
	@for d in $(SNAPSHOT_DEVICES); do \
		arn=$$(eval echo "\$$SNAPSHOT_ARN_$$d"); \
		echo "  fetching $$d ($$arn)"; \
		aws braket get-device --profile "$(AWS_PROFILE_RO)" --region "$(SNAPSHOT_REGION)" \
			--device-arn "$$arn" --output json \
			| $(COMPOSE) run --rm -T local shor-braket snapshot-import --device "$$d" || exit 1; \
		if [ -n "$(AWS_ACCOUNT_ID)" ] && [ "$(AWS_ACCOUNT_ID)" != "000000000000" ] \
			&& grep -q "$(AWS_ACCOUNT_ID)" "devices/snapshots/$$d.json"; then \
			echo "  devices/snapshots/$$d.json contains the account id. Refusing to keep it."; \
			rm -f "devices/snapshots/$$d.json"; exit 1; \
		fi; \
	done

.PHONY: device-info
device-info:  ## 保存済みスナップショットからデバイスの校正・価格・実行窓を表示する (例: make device-info DEVICE=garnet)
	$(LOCAL_RUN) shor-braket snapshot-show --device "$(DEVICE)"

.PHONY: submit-sv1
submit-sv1:  ## オンデマンドシミュレータ SV1 に投入する (⚠️ 課金あり・要 validated レコード)
	$(call notimpl,make submit-sv1,docs/03-execution-gate.md)

.PHONY: submit-qpu
submit-qpu:  ## 実機 QPU への投入を試みる (⚠️ 課金対象・要 validated レコード。現状は preflight まで。DEVICE / ORACLE / SHOTS)
	$(LOCAL_RUN) shor-braket submit-qpu --device "$(DEVICE)" --oracle "$(ORACLE)" \
		--shots "$(SHOTS)"

.PHONY: task-status
task-status:  ## 投入済みタスクの状態を確認する
	$(call notimpl,make task-status,docs/03-execution-gate.md)

.PHONY: report
report:  ## 実行結果を理想分布と比較してレポートを生成する
	$(call notimpl,make report,docs/02-architecture.md)
