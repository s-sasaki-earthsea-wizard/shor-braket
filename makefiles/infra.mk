# Terraform — AWS リソース管理 / IAM ポリシー

TF_DIR     ?= infra/terraform
IAM_DIR    ?= infra/iam

# Values come from .env (gitignored). CLI args still override them.
ACCOUNT_ID ?= $(AWS_ACCOUNT_ID)
BUCKET     ?= $(BRAKET_RESULTS_BUCKET)
PRINCIPAL  ?= $(IAM_PRINCIPAL)

define require_env
	@test -n "$($(1))" || { \
		echo ""; \
		echo "  $(1) is not set."; \
		echo "  .env に $(2) を設定するか、make $(3) $(1)=... で指定すること。"; \
		echo "  テンプレート: .env.example"; \
		echo ""; \
		exit 1; }
endef

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
	@echo "  expected : allowed for iqm/rigetti/simulator, explicitDeny for ionq/aqt"
	@echo ""
	@for arn in \
		"arn:aws:braket:eu-north-1::device/qpu/iqm/Garnet" \
		"arn:aws:braket:us-west-1::device/qpu/rigetti/Cepheus-1-108Q" \
		"arn:aws:braket:::device/quantum-simulator/amazon/sv1" \
		"arn:aws:braket:us-east-1::device/qpu/ionq/Forte-Enterprise-1" \
		"arn:aws:braket:eu-north-1::device/qpu/aqt/Ibex-Q1" ; do \
		d=$$(aws iam simulate-principal-policy \
			--policy-source-arn arn:aws:iam::$(ACCOUNT_ID):$(PRINCIPAL) \
			--action-names braket:CreateQuantumTask \
			--resource-arns "$$arn" \
			--context-entries ContextKeyName=aws:MultiFactorAuthPresent,ContextKeyValues=true,ContextKeyType=boolean \
			--query 'EvaluationResults[0].EvalDecision' --output text 2>&1 | tail -1); \
		printf "  %-16s %s\n" "$$d" "$$arn"; \
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

.PHONY: tf-fmt
tf-fmt:  ## Terraform のコードを整形する
	terraform -chdir=$(TF_DIR) fmt -recursive

.PHONY: tf-init
tf-init:  ## Terraform を初期化する
	terraform -chdir=$(TF_DIR) init

.PHONY: tf-validate
tf-validate:  ## Terraform の構文を検証する
	terraform -chdir=$(TF_DIR) validate

.PHONY: tf-plan
tf-plan:  ## Terraform の変更計画を表示する
	terraform -chdir=$(TF_DIR) plan -out=tfplan

.PHONY: tf-apply
tf-apply:  ## Terraform の変更を適用する (tf-plan の出力を使う)
	terraform -chdir=$(TF_DIR) apply tfplan

.PHONY: tf-destroy
tf-destroy:  ## Terraform で作成したリソースを破棄する (⚠️ S3 の結果も消える)
	terraform -chdir=$(TF_DIR) destroy
