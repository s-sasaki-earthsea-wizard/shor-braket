# Terraform — AWS リソース管理 / IAM ポリシー

TF_DIR     ?= infra/terraform
IAM_DIR    ?= infra/iam

# Values come from .env (gitignored). CLI args still override them.
ACCOUNT_ID ?= $(AWS_ACCOUNT_ID)
BUCKET     ?= $(BRAKET_RESULTS_BUCKET)
PRINCIPAL  ?= $(IAM_PRINCIPAL)
MONITOR_PRINCIPAL ?= $(IAM_MONITOR_PRINCIPAL)
# Terraform runs under the MFA-backed admin profile. Never root.
TF_PROFILE ?= $(AWS_PROFILE_ADMIN)

# The one-time bootstrap and retire-user need a credential that ALREADY has IAM
# admin rights. That is not the project's AWS_PROFILE: the Makefile exports every
# .env value, so AWS_PROFILE=shor-braket-ro would leak into these targets, and
# that profile does not exist until Terraform has created the users.
# Before the bootstrap this is the account's existing admin credential; after it,
# pass BOOTSTRAP_PROFILE=admin.
BOOTSTRAP_PROFILE ?= default

# Refuse to run Terraform with root credentials, whatever the profile is called.
define refuse_root
	@arn=$$(AWS_PROFILE=$(TF_PROFILE) aws sts get-caller-identity --query Arn --output text 2>/dev/null); \
	case "$$arn" in \
		*:root) echo ""; echo "  $(TF_PROFILE) resolves to the root account. Refusing."; \
		        echo "  infra/iam/README.md §11 に従って管理者ロールを作り、AWS_PROFILE_ADMIN を差し替えること。"; echo ""; exit 1 ;; \
		"")     echo ""; echo "  Cannot resolve caller identity for profile $(TF_PROFILE)."; echo ""; exit 1 ;; \
		*)      echo "  caller: $$arn" ;; \
	esac
endef

# The Terraform AWS provider cannot prompt for an MFA code: it has no
# AssumeRoleTokenProvider, so a profile carrying mfa_serial fails with
# "assume role with MFA enabled, but AssumeRoleTokenProvider session option not set".
# The AWS CLI can prompt, and caches the assumed-role session, so resolve the
# profile to temporary credentials there and hand those to Terraform.
# AWS_PROFILE is unset afterwards so the provider cannot re-attempt the assume.
define tf_run
	@creds="$$(aws configure export-credentials --profile $(TF_PROFILE) --format env)" || { \
		echo ""; \
		echo "  Could not resolve credentials for profile $(TF_PROFILE)."; \
		echo "  Check it with: aws sts get-caller-identity --profile $(TF_PROFILE)"; \
		echo ""; \
		exit 1; }; \
	eval "$$creds"; \
	unset AWS_PROFILE; \
	terraform -chdir=$(TF_DIR) $(1)
endef

define require_env
	@test -n "$($(1))" || { \
		echo ""; \
		echo "  $(1) is not set."; \
		echo "  .env に $(2) を設定するか、make $(3) $(1)=... で指定すること。"; \
		echo "  テンプレート: .env.example"; \
		echo ""; \
		exit 1; }
endef

.PHONY: bootstrap-admin
bootstrap-admin:  ## ⚠️ 一度きり: 管理者を MFA 必須の assume role にする (CHECK=1 で確認のみ、BOOTSTRAP_PROFILE で資格を指定)
	@AWS_PROFILE=$(BOOTSTRAP_PROFILE) bash infra/iam/bootstrap-admin.sh $(if $(CHECK),--check,)

.PHONY: issue-creds
issue-creds:  ## tf-apply 後: IAM ユーザーのアクセスキーを発行しプロファイルに書く (例: make issue-creds IAM_USER=shor-braket-operator PROFILE_NAME=shor-braket-ro MFA=1)
	@test -n "$(IAM_USER)" && test -n "$(PROFILE_NAME)" || { \
		echo ""; \
		echo "  IAM_USER と PROFILE_NAME を指定すること。"; \
		echo "  operator: make issue-creds IAM_USER=shor-braket-operator PROFILE_NAME=shor-braket-ro MFA=1"; \
		echo "  monitor : make issue-creds IAM_USER=shor-braket-monitor PROFILE_NAME=shor-braket-monitor"; \
		echo ""; \
		exit 1; }
	@ADMIN_PROFILE=$(TF_PROFILE) REGION=$(AWS_REGION) \
		bash infra/iam/issue-user-credentials.sh "$(IAM_USER)" "$(PROFILE_NAME)" $(if $(MFA),--mfa,)

.PHONY: retire-user
# RETIRE_USER, not USER: the shell exports USER as the login name, so a bare
# `make retire-user` would silently target it.
retire-user:  ## ⚠️ 不要になった IAM ユーザーを削除する (例: make retire-user RETIRE_USER=terraform-admin)
	@test -n "$(RETIRE_USER)" || { \
		echo ""; \
		echo "  RETIRE_USER is not set."; \
		echo "  例: make retire-user RETIRE_USER=terraform-admin"; \
		echo ""; \
		exit 1; }
	@AWS_PROFILE=$(BOOTSTRAP_PROFILE) bash infra/iam/bootstrap-admin.sh --retire "$(RETIRE_USER)"

.PHONY: iam-lint
iam-lint:  ## IAM ポリシー JSON の構文を検証する
	@for f in $(IAM_DIR)/*.json; do \
		python3 -c "import json,sys; json.load(open('$$f'))" && echo "  ok  $$f" || exit 1; \
	done

.PHONY: iam-render
iam-render:  ## IAM ポリシーのプレースホルダを .env の値で置換する
	$(call require_env,ACCOUNT_ID,AWS_ACCOUNT_ID,iam-render)
	$(call require_env,BUCKET,BRAKET_RESULTS_BUCKET,iam-render)
	@mkdir -p $(IAM_DIR)/rendered
	@for f in $(IAM_DIR)/*.json; do \
		out=$(IAM_DIR)/rendered/$$(basename $$f); \
		sed -e 's|<AWS_ACCOUNT_ID>|$(ACCOUNT_ID)|g' -e 's|<RESULTS_BUCKET>|$(BUCKET)|g' $$f > $$out; \
		python3 -c "import json,sys; json.load(open('$$out'))" || exit 1; \
		echo "  rendered  $$out"; \
	done

.PHONY: iam-verify
iam-verify:  ## IAM ポリシーの効果をシミュレータで検証する (課金なし)
	$(call require_env,ACCOUNT_ID,AWS_ACCOUNT_ID,iam-verify)
	$(call require_env,PRINCIPAL,IAM_PRINCIPAL,iam-verify)
	@echo ""
	@echo "  principal: arn:aws:iam::$(ACCOUNT_ID):$(PRINCIPAL)  (MFA present = true)"
	@echo "  expected : allowed for iqm/rigetti/simulator, explicitDeny for ionq"
	@echo ""
	@for arn in \
		"arn:aws:braket:eu-north-1::device/qpu/iqm/Garnet" \
		"arn:aws:braket:us-west-1::device/qpu/rigetti/Cepheus-1-108Q" \
		"arn:aws:braket:::device/quantum-simulator/amazon/sv1" \
		"arn:aws:braket:us-east-1::device/qpu/ionq/Forte-Enterprise-1" ; do \
		d=$$(aws iam simulate-principal-policy \
			--policy-source-arn arn:aws:iam::$(ACCOUNT_ID):$(PRINCIPAL) \
			--action-names braket:CreateQuantumTask \
			--resource-arns "$$arn" \
			--context-entries ContextKeyName=aws:MultiFactorAuthPresent,ContextKeyValues=true,ContextKeyType=boolean \
			--query 'EvaluationResults[0].EvalDecision' --output text 2>&1 | tail -1); \
		printf "  %-16s %s\n" "$$d" "$$arn"; \
	done
	@echo ""
	@echo "  AQT のタグゲートを確認 (MFA あり)"
	@echo "  期待値: タグなし explicitDeny / campaign=device-comparison allowed / 別の値 explicitDeny"
	@echo ""
	@for tag in "" "device-comparison" "wrong-value"; do \
		if [ -z "$$tag" ]; then ctx=""; label="(no tag)"; \
		else ctx="ContextKeyName=aws:RequestTag/campaign,ContextKeyValues=$$tag,ContextKeyType=string"; label="campaign=$$tag"; fi; \
		d=$$(aws iam simulate-principal-policy \
			--policy-source-arn arn:aws:iam::$(ACCOUNT_ID):$(PRINCIPAL) \
			--action-names braket:CreateQuantumTask \
			--resource-arns "arn:aws:braket:eu-north-1::device/qpu/aqt/Ibex-Q1" \
			--context-entries ContextKeyName=aws:MultiFactorAuthPresent,ContextKeyValues=true,ContextKeyType=boolean $$ctx \
			--query 'EvaluationResults[0].EvalDecision' --output text 2>&1 | tail -1); \
		printf "  %-16s %s\n" "$$d" "aqt/Ibex-Q1 $$label"; \
	done
	@echo ""
	@echo "  MFA なしでの拒否を確認 (期待値: すべて explicitDeny)"
	@echo ""
	@for arn in \
		"arn:aws:braket:eu-north-1::device/qpu/iqm/Garnet" \
		"arn:aws:braket:::device/quantum-simulator/amazon/sv1" ; do \
		d=$$(aws iam simulate-principal-policy \
			--policy-source-arn arn:aws:iam::$(ACCOUNT_ID):$(PRINCIPAL) \
			--action-names braket:CreateQuantumTask \
			--resource-arns "$$arn" \
			--context-entries ContextKeyName=aws:MultiFactorAuthPresent,ContextKeyValues=false,ContextKeyType=boolean \
			--query 'EvaluationResults[0].EvalDecision' --output text 2>&1 | tail -1); \
		printf "  %-16s %s\n" "$$d" "$$arn"; \
	done
	@echo ""
	@if [ -n "$(MONITOR_PRINCIPAL)" ]; then \
		echo "  監視ユーザーに実行権限がないことを確認 (MFA あり, 期待値: すべて implicitDeny)"; \
		echo "  principal: arn:aws:iam::$(ACCOUNT_ID):$(MONITOR_PRINCIPAL)"; \
		echo ""; \
		for arn in \
			"arn:aws:braket:eu-north-1::device/qpu/iqm/Garnet" \
			"arn:aws:braket:::device/quantum-simulator/amazon/sv1" ; do \
			d=$$(aws iam simulate-principal-policy \
				--policy-source-arn arn:aws:iam::$(ACCOUNT_ID):$(MONITOR_PRINCIPAL) \
				--action-names braket:CreateQuantumTask \
				--resource-arns "$$arn" \
				--context-entries ContextKeyName=aws:MultiFactorAuthPresent,ContextKeyValues=true,ContextKeyType=boolean \
				--query 'EvaluationResults[0].EvalDecision' --output text 2>&1 | tail -1); \
			printf "  %-16s %s\n" "$$d" "$$arn"; \
		done; \
		d=$$(aws iam simulate-principal-policy \
			--policy-source-arn arn:aws:iam::$(ACCOUNT_ID):$(MONITOR_PRINCIPAL) \
			--action-names sts:AssumeRole \
			--resource-arns "arn:aws:iam::$(ACCOUNT_ID):role/ShorBraketExecutionRole" \
			--query 'EvaluationResults[0].EvalDecision' --output text 2>&1 | tail -1); \
		printf "  %-16s %s\n" "$$d" "sts:AssumeRole -> role/ShorBraketExecutionRole"; \
		echo ""; \
	fi

.PHONY: tf-fmt
tf-fmt:  ## Terraform のコードを整形する
	terraform -chdir=$(TF_DIR) fmt -recursive

.PHONY: tf-init
tf-init:  ## Terraform を初期化する
	$(call require_env,TF_PROFILE,AWS_PROFILE_ADMIN,tf-init)
	AWS_PROFILE=$(TF_PROFILE) terraform -chdir=$(TF_DIR) init

.PHONY: tf-validate
tf-validate:  ## Terraform の構文を検証する
	terraform -chdir=$(TF_DIR) validate

.PHONY: tf-plan
tf-plan:  ## Terraform の変更計画を表示する (AWS_PROFILE_ADMIN を使う。root は拒否)
	$(call require_env,TF_PROFILE,AWS_PROFILE_ADMIN,tf-plan)
	$(refuse_root)
	$(call tf_run,plan -out=tfplan)

.PHONY: tf-apply
tf-apply:  ## Terraform の変更を適用する (tf-plan の出力を使う。AWS_PROFILE_ADMIN)
	$(call require_env,TF_PROFILE,AWS_PROFILE_ADMIN,tf-apply)
	$(refuse_root)
	$(call tf_run,apply tfplan)

.PHONY: tf-destroy
tf-destroy:  ## Terraform で作成したリソースを破棄する (⚠️ S3 の結果も消える)
	$(call require_env,TF_PROFILE,AWS_PROFILE_ADMIN,tf-destroy)
	$(refuse_root)
	$(call tf_run,destroy)
