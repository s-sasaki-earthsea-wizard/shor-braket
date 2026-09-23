#!/usr/bin/env bash
#
# Measure what the IAM guardrails actually do, using the policy simulator.
#
# No quantum task is created and nothing is charged: simulate-principal-policy only
# evaluates policy documents. It does need iam:SimulatePrincipalPolicy and
# iam:GetContextKeysForPrincipalPolicy, both of which the readonly policy grants.
#
# Since ADR-0004 the capability to reach AQT lives in a separate role rather than in a
# request tag, so there are two roles to check and their expectations are mirror images:
#
#   ShorBraketExecutionRole   IQM allowed, AQT denied
#   ShorBraketAqtRole         AQT allowed, IQM denied
#
# The simulator answers for the context it is given, so the context has to be the one AWS
# really supplies. Until 2026-09-23 the roles were simulated with MultiFactorAuthPresent=true,
# which is not what an MFA-assumed session carries: inside the role the key evaluates as
# false, CloudTrail records mfaAuthenticated=false, and a real Garnet task was refused by an
# MFA deny that the simulator had reported as harmless. Roles are now simulated with the key
# false, and MFA is checked where it is actually enforced, by really calling AssumeRole
# without it (section 3). Device ARNs carry the account id for the same reason: that is the
# resource the service evaluates, even though the request names the device without one.
#
# Usage:
#   bash infra/iam/verify-guardrails.sh
#
# Environment (make iam-verify passes these from .env):
#   ACCOUNT_ID          AWS account id                    (required)
#   EXEC_PRINCIPAL      role/... or user/...              (default: role/ShorBraketExecutionRole)
#   AQT_PRINCIPAL       role/... or user/...              (default: role/ShorBraketAqtRole)
#   OPERATOR_PRINCIPAL  operator user, optional           (default: user/shor-braket-operator)
#   MONITOR_PRINCIPAL   monitor user, optional            (skipped when empty)
#
# Exit status is 1 if any expectation is not met, so this can gate a change.

set -euo pipefail

ACCOUNT_ID="${ACCOUNT_ID:?ACCOUNT_ID is required}"
# Profile holding the operator's long-term keys, for the real AssumeRole probe in section 3.
PROBE_PROFILE="${PROBE_PROFILE:-${AWS_PROFILE:-}}"
EXEC_PRINCIPAL="${EXEC_PRINCIPAL:-role/ShorBraketExecutionRole}"
AQT_PRINCIPAL="${AQT_PRINCIPAL:-role/ShorBraketAqtRole}"
OPERATOR_PRINCIPAL="${OPERATOR_PRINCIPAL:-user/shor-braket-operator}"
MONITOR_PRINCIPAL="${MONITOR_PRINCIPAL:-}"

GARNET="arn:aws:braket:eu-north-1:${ACCOUNT_ID}:device/qpu/iqm/Garnet"
EMERALD="arn:aws:braket:eu-north-1:${ACCOUNT_ID}:device/qpu/iqm/Emerald"
IBEX="arn:aws:braket:eu-north-1:${ACCOUNT_ID}:device/qpu/aqt/Ibex-Q1"
CEPHEUS="arn:aws:braket:us-west-1:${ACCOUNT_ID}:device/qpu/rigetti/Cepheus-1-108Q"
IONQ="arn:aws:braket:us-east-1:${ACCOUNT_ID}:device/qpu/ionq/Forte-Enterprise-1"

MFA_TRUE="ContextKeyName=aws:MultiFactorAuthPresent,ContextKeyValues=true,ContextKeyType=boolean"
MFA_FALSE="ContextKeyName=aws:MultiFactorAuthPresent,ContextKeyValues=false,ContextKeyType=boolean"

# What an MFA-assumed role session actually carries (measured 2026-09-23).
ROLE_CTX="$MFA_FALSE"

failures=0

# decide <principal> <action> <resource-arn> [context-entry]
decide() {
	local principal="$1" action="$2" resource="$3" context="${4:-}"
	local -a args=(
		--policy-source-arn "arn:aws:iam::${ACCOUNT_ID}:${principal}"
		--action-names "$action"
		--resource-arns "$resource"
	)
	if [ -n "$context" ]; then
		args+=(--context-entries "$context")
	fi
	# An AWS-side failure must be reported as one bad row, not kill the whole run:
	# pipefail would otherwise propagate it through set -e.
	local out
	out="$(aws iam simulate-principal-policy "${args[@]}" \
		--query 'EvaluationResults[0].EvalDecision' --output text 2>&1 | tail -1)" || out="error"
	printf '%s' "${out:-error}"
}

# expect <expected> <label> <principal> <action> <resource> [context]
expect() {
	local expected="$1" label="$2"
	shift 2
	local actual
	actual="$(decide "$@")"
	if [ "$actual" = "$expected" ]; then
		printf "  \033[32mok  \033[0m %-14s %-12s %s\n" "$actual" "(want $expected)" "$label"
	else
		printf "  \033[31mFAIL\033[0m %-14s %-12s %s\n" "$actual" "(want $expected)" "$label"
		failures=$((failures + 1))
	fi
}

# probe_assume_without_mfa <label> <role-principal>
# Really call AssumeRole with long-term keys and no MFA. The trust policy must refuse it.
# If it is ever granted, the temporary credentials are discarded unused and the row fails.
probe_assume_without_mfa() {
	local label="$1" principal="$2" out verdict
	if [ -z "$PROBE_PROFILE" ]; then
		printf "  \033[33mskip\033[0m %-27s %s (PROBE_PROFILE / AWS_PROFILE is empty)\n" "" "$label"
		return
	fi
	out="$(aws sts assume-role --profile "$PROBE_PROFILE" \
		--role-arn "arn:aws:iam::${ACCOUNT_ID}:${principal}" \
		--role-session-name iam-verify-no-mfa-probe \
		--query 'AssumedRoleUser.Arn' --output text 2>&1)" || true
	if printf '%s' "$out" | grep -q 'AccessDenied'; then
		verdict="AccessDenied"
		printf "  \033[32mok  \033[0m %-14s %-12s %s\n" "$verdict" "(want AccessDenied)" "$label"
	else
		verdict="$(printf '%s' "$out" | grep -q 'assumed-role' && echo granted || echo error)"
		printf "  \033[31mFAIL\033[0m %-14s %-12s %s\n" "$verdict" "(want AccessDenied)" "$label"
		failures=$((failures + 1))
	fi
}

section() {
	printf "\n  \033[1m%s\033[0m\n" "$1"
	printf "  %s\n\n" "$2"
}

section "1. 実行ロール: IQM を通し、AQT を拒否する" \
	"principal: ${EXEC_PRINCIPAL}  (MFA present = false: assumed-role sessions carry it so)"
expect allowed "IQM Garnet" "$EXEC_PRINCIPAL" braket:CreateQuantumTask "$GARNET" "$ROLE_CTX"
expect allowed "IQM Emerald" "$EXEC_PRINCIPAL" braket:CreateQuantumTask "$EMERALD" "$ROLE_CTX"
expect allowed "Rigetti Cepheus" "$EXEC_PRINCIPAL" braket:CreateQuantumTask "$CEPHEUS" "$ROLE_CTX"
expect explicitDeny "AQT IBEX  <- ADR-0004" "$EXEC_PRINCIPAL" braket:CreateQuantumTask "$IBEX" "$ROLE_CTX"
expect explicitDeny "IonQ Forte Enterprise" "$EXEC_PRINCIPAL" braket:CreateQuantumTask "$IONQ" "$ROLE_CTX"

section "2. AQT ロール: AQT だけを通す" \
	"principal: ${AQT_PRINCIPAL}  (MFA present = false: assumed-role sessions carry it so)"
expect allowed "AQT IBEX" "$AQT_PRINCIPAL" braket:CreateQuantumTask "$IBEX" "$ROLE_CTX"
expect explicitDeny "IQM Garnet  <- ADR-0004" "$AQT_PRINCIPAL" braket:CreateQuantumTask "$GARNET" "$ROLE_CTX"
expect explicitDeny "IQM Emerald <- ADR-0004" "$AQT_PRINCIPAL" braket:CreateQuantumTask "$EMERALD" "$ROLE_CTX"
expect explicitDeny "IonQ Forte Enterprise" "$AQT_PRINCIPAL" braket:CreateQuantumTask "$IONQ" "$ROLE_CTX"

section "3. MFA は信頼ポリシーが強制する（実際に AssumeRole を呼ぶ）" \
	"長期キーで MFA なしの assume を試みる。期待値: どちらも AccessDenied。課金なし"
probe_assume_without_mfa "operator -> exec, no MFA" "$EXEC_PRINCIPAL"
probe_assume_without_mfa "operator -> aqt,  no MFA" "$AQT_PRINCIPAL"

if [ -n "$OPERATOR_PRINCIPAL" ]; then
	section "4. 操作者の長期キーからは高額機に触れない" \
		"principal: ${OPERATOR_PRINCIPAL}  (MFA present = true)"
	expect explicitDeny "AQT IBEX" "$OPERATOR_PRINCIPAL" braket:CreateQuantumTask "$IBEX" "$MFA_TRUE"
	expect explicitDeny "IQM Garnet, long-term key" "$OPERATOR_PRINCIPAL" braket:CreateQuantumTask \
		"$GARNET"
	expect allowed "AssumeRole -> exec" "$OPERATOR_PRINCIPAL" sts:AssumeRole \
		"arn:aws:iam::${ACCOUNT_ID}:${EXEC_PRINCIPAL}"
	expect allowed "AssumeRole -> aqt" "$OPERATOR_PRINCIPAL" sts:AssumeRole \
		"arn:aws:iam::${ACCOUNT_ID}:${AQT_PRINCIPAL}"
fi

if [ -n "$MONITOR_PRINCIPAL" ]; then
	section "5. 監視ユーザーは実行も assume もできない" \
		"principal: ${MONITOR_PRINCIPAL}  (MFA present = true)"
	expect implicitDeny "IQM Garnet" "$MONITOR_PRINCIPAL" braket:CreateQuantumTask "$GARNET" "$MFA_TRUE"
	expect implicitDeny "AssumeRole -> exec" "$MONITOR_PRINCIPAL" sts:AssumeRole \
		"arn:aws:iam::${ACCOUNT_ID}:${EXEC_PRINCIPAL}"
	expect implicitDeny "AssumeRole -> aqt" "$MONITOR_PRINCIPAL" sts:AssumeRole \
		"arn:aws:iam::${ACCOUNT_ID}:${AQT_PRINCIPAL}"
fi

section "6. Spending Limit は誰でも読めるが、誰も変えられない" \
	"期待値: SearchSpendingLimits は allowed、UpdateSpendingLimit は explicitDeny（guardrail）"
expect allowed "exec / Search" "$EXEC_PRINCIPAL" braket:SearchSpendingLimits "*" "$ROLE_CTX"
expect explicitDeny "exec / Update" "$EXEC_PRINCIPAL" braket:UpdateSpendingLimit "*" "$ROLE_CTX"
expect allowed "aqt  / Search" "$AQT_PRINCIPAL" braket:SearchSpendingLimits "*" "$ROLE_CTX"
expect explicitDeny "aqt  / Delete" "$AQT_PRINCIPAL" braket:DeleteSpendingLimit "*" "$ROLE_CTX"
if [ -n "$OPERATOR_PRINCIPAL" ]; then
	expect allowed "operator / Search" "$OPERATOR_PRINCIPAL" braket:SearchSpendingLimits "*" "$MFA_TRUE"
	expect explicitDeny "operator / Create" "$OPERATOR_PRINCIPAL" braket:CreateSpendingLimit "*" "$MFA_TRUE"
fi
if [ -n "$MONITOR_PRINCIPAL" ]; then
	expect allowed "monitor / Search" "$MONITOR_PRINCIPAL" braket:SearchSpendingLimits "*" "$MFA_TRUE"
	expect explicitDeny "monitor / Update" "$MONITOR_PRINCIPAL" braket:UpdateSpendingLimit "*" "$MFA_TRUE"
fi

echo ""
if [ "$failures" -eq 0 ]; then
	printf "  \033[32mすべて期待どおり。\033[0m infra/iam/README.md §7 の表に結果を記録すること。\n\n"
else
	printf "  \033[31m%d 件が期待と違う。\033[0m 適用済みのポリシーと infra/iam/*.json を突き合わせること。\n\n" "$failures"
	exit 1
fi
