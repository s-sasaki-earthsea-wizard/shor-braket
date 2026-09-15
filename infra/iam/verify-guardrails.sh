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
EXEC_PRINCIPAL="${EXEC_PRINCIPAL:-role/ShorBraketExecutionRole}"
AQT_PRINCIPAL="${AQT_PRINCIPAL:-role/ShorBraketAqtRole}"
OPERATOR_PRINCIPAL="${OPERATOR_PRINCIPAL:-user/shor-braket-operator}"
MONITOR_PRINCIPAL="${MONITOR_PRINCIPAL:-}"

GARNET="arn:aws:braket:eu-north-1::device/qpu/iqm/Garnet"
EMERALD="arn:aws:braket:eu-north-1::device/qpu/iqm/Emerald"
IBEX="arn:aws:braket:eu-north-1::device/qpu/aqt/Ibex-Q1"
CEPHEUS="arn:aws:braket:us-west-1::device/qpu/rigetti/Cepheus-1-108Q"
IONQ="arn:aws:braket:us-east-1::device/qpu/ionq/Forte-Enterprise-1"

MFA_TRUE="ContextKeyName=aws:MultiFactorAuthPresent,ContextKeyValues=true,ContextKeyType=boolean"
MFA_FALSE="ContextKeyName=aws:MultiFactorAuthPresent,ContextKeyValues=false,ContextKeyType=boolean"

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

section() {
	printf "\n  \033[1m%s\033[0m\n" "$1"
	printf "  %s\n\n" "$2"
}

section "1. 実行ロール: IQM を通し、AQT を拒否する" \
	"principal: ${EXEC_PRINCIPAL}  (MFA present = true)"
expect allowed "IQM Garnet" "$EXEC_PRINCIPAL" braket:CreateQuantumTask "$GARNET" "$MFA_TRUE"
expect allowed "IQM Emerald" "$EXEC_PRINCIPAL" braket:CreateQuantumTask "$EMERALD" "$MFA_TRUE"
expect allowed "Rigetti Cepheus" "$EXEC_PRINCIPAL" braket:CreateQuantumTask "$CEPHEUS" "$MFA_TRUE"
expect explicitDeny "AQT IBEX  <- ADR-0004" "$EXEC_PRINCIPAL" braket:CreateQuantumTask "$IBEX" "$MFA_TRUE"
expect explicitDeny "IonQ Forte Enterprise" "$EXEC_PRINCIPAL" braket:CreateQuantumTask "$IONQ" "$MFA_TRUE"

section "2. AQT ロール: AQT だけを通す" \
	"principal: ${AQT_PRINCIPAL}  (MFA present = true)"
expect allowed "AQT IBEX" "$AQT_PRINCIPAL" braket:CreateQuantumTask "$IBEX" "$MFA_TRUE"
expect explicitDeny "IQM Garnet  <- ADR-0004" "$AQT_PRINCIPAL" braket:CreateQuantumTask "$GARNET" "$MFA_TRUE"
expect explicitDeny "IQM Emerald <- ADR-0004" "$AQT_PRINCIPAL" braket:CreateQuantumTask "$EMERALD" "$MFA_TRUE"
expect explicitDeny "IonQ Forte Enterprise" "$AQT_PRINCIPAL" braket:CreateQuantumTask "$IONQ" "$MFA_TRUE"

section "3. MFA なしはどちらのロールでも拒否される" \
	"期待値: すべて explicitDeny"
expect explicitDeny "exec / IQM Garnet" "$EXEC_PRINCIPAL" braket:CreateQuantumTask "$GARNET" "$MFA_FALSE"
expect explicitDeny "aqt / AQT IBEX" "$AQT_PRINCIPAL" braket:CreateQuantumTask "$IBEX" "$MFA_FALSE"

if [ -n "$OPERATOR_PRINCIPAL" ]; then
	section "4. 操作者の長期キーからは高額機に触れない" \
		"principal: ${OPERATOR_PRINCIPAL}  (MFA present = true)"
	expect explicitDeny "AQT IBEX" "$OPERATOR_PRINCIPAL" braket:CreateQuantumTask "$IBEX" "$MFA_TRUE"
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

echo ""
if [ "$failures" -eq 0 ]; then
	printf "  \033[32mすべて期待どおり。\033[0m issue #2 の表に結果を記録すること。\n\n"
else
	printf "  \033[31m%d 件が期待と違う。\033[0m 適用済みのポリシーと infra/iam/*.json を突き合わせること。\n\n" "$failures"
	exit 1
fi
