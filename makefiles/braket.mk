# Amazon Braket — ⚠️ 課金が発生する。実行ゲートを必ず経由すること
#
# プロファイル分割:
#   読み取り系 (devices / device-snapshot / task-status) → AWS_PROFILE_RO  (MFA 不要)
#   投入系 (submit-qpu, IQM)                          → AWS_PROFILE_EXEC (MFA 必須)
#   投入系 (submit-qpu, AQT IBEX)                     → AWS_PROFILE_AQT  (MFA 必須・別ロール)
# 読み取りプロファイルで submit を叩くと IAM が AccessDenied を返す。これは仕様。
# 実行ロールは AQT を無条件 Deny、AQT ロールは IQM を無条件 Deny する (ADR-0004)。

# Discovery only. The project runs exclusively in eu-north-1 (ADR-0004); the other
# regions are listed so `make devices` can still show what exists elsewhere.
BRAKET_REGIONS ?= eu-north-1 us-east-1 us-west-1

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
	$(call require_env_file,make devices)
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
	$(call require_env_file,make device-snapshot)
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

# The campaign tag is passed as an argument rather than left to the environment: the offline
# `local` container does not receive BRAKET_CAMPAIGN, and a dry run that shows different tags
# from the paid run is not a dry run of that run.
.PHONY: preflight
preflight:  ## 投入ゲートを最後まで回す (無料・オフライン・タスクは作らない。DEVICE / ORACLE / SHOTS / BRAKET_CAMPAIGN)
	$(call require_env_file,make preflight)
	$(LOCAL_RUN) shor-braket submit-qpu --device "$(DEVICE)" --oracle "$(ORACLE)" \
		--shots "$(SHOTS)" --campaign "$(BRAKET_CAMPAIGN)" --no-execute

.PHONY: submit-qpu
submit-qpu:  ## ⚠️ 課金対象: 実機 QPU に量子タスクを投入する (要 validated レコード + MFA。DEVICE / ORACLE / SHOTS / BRAKET_CAMPAIGN)
	$(call require_env_file,make submit-qpu)
	$(require_exec_profile)
	$(require_home)
	$(AWS_RUN) shor-braket submit-qpu --device "$(DEVICE)" --oracle "$(ORACLE)" \
		--shots "$(SHOTS)" --campaign "$(BRAKET_CAMPAIGN)" --execute

.PHONY: task-status
task-status:  ## 投入済みタスクの状態を確認する (読み取りのみ・課金なし・MFA 不要。TASK_ARN で 1 件指定)
	$(call require_env_file,make task-status)
	$(require_ro_profile)
	$(require_home)
	$(AWS_RUN) shor-braket task-status $(if $(TASK_ARN),--task-arn "$(TASK_ARN)",)

.PHONY: report
report:  ## 実行結果を理想分布と比較してレポートを生成する (読み取りのみ・課金なし。TASK_ARN / RESULT_FILE)
	$(call require_env_file,make report)
ifneq (,$(RESULT_FILE))
	$(LOCAL_RUN) shor-braket report --result-file "$(RESULT_FILE)" \
		$(if $(TASK_ARN),--task-arn "$(TASK_ARN)",)
else
	$(require_ro_profile)
	$(require_home)
	$(AWS_RUN) shor-braket report $(if $(TASK_ARN),--task-arn "$(TASK_ARN)",)
endif
