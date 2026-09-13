#!/usr/bin/env bash
#
# One-time account bootstrap: put administrative access behind MFA so the root
# access key, and any standing admin user, can be retired.
#
# Target shape:
#
#   IAM User admin-base      long-term key, MFA device, may only assume
#     |  sts:AssumeRole (MFA required, 1 hour)
#     v
#   IAM Role AdminRole       AdministratorAccess
#
# A leaked long-term key alone then grants nothing: the role needs an MFA code.
#
# Usage:
#   bash infra/iam/bootstrap-admin.sh --check          report state, change nothing
#   bash infra/iam/bootstrap-admin.sh                  create the MFA-gated admin path
#   bash infra/iam/bootstrap-admin.sh --retire USER    delete a retired admin user
#
# Order matters. Run the bootstrap, verify the new profile, and only then retire
# the old principals. Deleting the root access key stays manual: see
# infra/iam/README.md section 11.5.
#
# Environment overrides:
#   BASE_USER    user to create or reuse         (default: admin-base)
#   ADMIN_ROLE   role to create                  (default: AdminRole)
#   PROFILE      assume profile in ~/.aws/config (default: admin)
#   SRC_PROFILE  profile holding the long-term key (default: <BASE_USER>)
#   REGION       region recorded in the profiles (default: ap-northeast-1)
#   MFA_NAME     virtual MFA device name         (default: <BASE_USER>)
#
# Do not paste this into an interactive zsh. `set -u` breaks the VS Code shell
# integration hook (__vsc_preexec: RPROMPT: parameter not set) and `set -e`
# closes the shell on the first non-zero status. Run it as a script.

set -euo pipefail

BASE_USER="${BASE_USER:-admin-base}"
ADMIN_ROLE="${ADMIN_ROLE:-AdminRole}"
PROFILE="${PROFILE:-admin}"
SRC_PROFILE="${SRC_PROFILE:-$BASE_USER}"
REGION="${REGION:-ap-northeast-1}"
MFA_NAME="${MFA_NAME:-$BASE_USER}"

ADMIN_POLICY_ARN="arn:aws:iam::aws:policy/AdministratorAccess"
INLINE_POLICY_NAME="AssumeAdminRole"
CREDS_FILE="${AWS_SHARED_CREDENTIALS_FILE:-$HOME/.aws/credentials}"

say()   { printf '  %s\n' "$*"; }
head_() { printf '\n== %s\n' "$*"; }
die()   { printf '\n  ERROR: %s\n\n' "$*" >&2; exit 1; }

command -v aws >/dev/null     || die "aws CLI not found."
command -v python3 >/dev/null || die "python3 not found (used to write credentials without exposing secrets in argv)."

# This script runs BEFORE the project's own profiles exist, so it must not
# inherit AWS_PROFILE from .env. `make bootstrap-admin` pins it via
# BOOTSTRAP_PROFILE; a direct invocation uses whatever is already in the
# environment.
if ! ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text 2>/dev/null)"; then
  printf '\n  ERROR: cannot resolve caller identity with profile %s\n\n' "'${AWS_PROFILE:-<default>}'" >&2
  printf '  This step needs a credential that already has IAM admin rights.\n' >&2
  printf '  The project profiles (shor-braket-ro / -exec) do not exist yet and\n' >&2
  printf '  cannot be used here. Pick the credential explicitly:\n\n' >&2
  printf '      make bootstrap-admin BOOTSTRAP_PROFILE=<profile>\n\n' >&2
  printf '  Available profiles: %s\n\n' "$(aws configure list-profiles 2>/dev/null | tr '\n' ' ')" >&2
  exit 1
fi
CALLER_ARN="$(aws sts get-caller-identity --query Arn --output text)"
redact() { sed -e "s/${ACCOUNT_ID}/<ACCOUNT_ID>/g" -e 's/AKIA[A-Z0-9]\{16\}/<AKID>/g'; }

ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${ADMIN_ROLE}"
MFA_SERIAL="arn:aws:iam::${ACCOUNT_ID}:mfa/${MFA_NAME}"

user_exists() { aws iam get-user --user-name "$1" >/dev/null 2>&1; }

# ======================================================== retire mode

if [ "${1:-}" = "--retire" ]; then
  VICTIM="${2:-}"
  [ -n "$VICTIM" ] || die "usage: $0 --retire <user-name>"
  [ "$VICTIM" != "$BASE_USER" ] || die "refusing to retire the bootstrap user $BASE_USER."

  head_ "Retire IAM user: $VICTIM"
  user_exists "$VICTIM" || die "user '$VICTIM' does not exist."

  say "caller : $(printf '%s' "$CALLER_ARN" | redact)"
  say ""
  say "Last activity:"
  for kid in $(aws iam list-access-keys --user-name "$VICTIM" \
                 --query 'AccessKeyMetadata[].AccessKeyId' --output text); do
    used="$(aws iam get-access-key-last-used --access-key-id "$kid" \
              --query 'AccessKeyLastUsed.LastUsedDate' --output text)"
    say "  key $(printf '%s' "$kid" | redact) last used: $used"
  done
  aws iam get-login-profile --user-name "$VICTIM" >/dev/null 2>&1 \
    && say "  console login profile: PRESENT" || say "  console login profile: none"

  say ""
  say "Will delete, in order: access keys, MFA devices, inline policies,"
  say "managed policy attachments, group memberships, login profile, then the user."
  printf '\n  Type the user name to confirm: '
  read -r confirm
  [ "$confirm" = "$VICTIM" ] || { printf '\n  Aborted.\n\n'; exit 1; }

  for kid in $(aws iam list-access-keys --user-name "$VICTIM" \
                 --query 'AccessKeyMetadata[].AccessKeyId' --output text); do
    aws iam delete-access-key --user-name "$VICTIM" --access-key-id "$kid"
    say "deleted access key $(printf '%s' "$kid" | redact)"
  done
  for ser in $(aws iam list-mfa-devices --user-name "$VICTIM" \
                 --query 'MFADevices[].SerialNumber' --output text); do
    aws iam deactivate-mfa-device --user-name "$VICTIM" --serial-number "$ser"
    case "$ser" in *:mfa/*) aws iam delete-virtual-mfa-device --serial-number "$ser" || true ;; esac
    say "removed MFA device"
  done
  for pol in $(aws iam list-user-policies --user-name "$VICTIM" --query 'PolicyNames[]' --output text); do
    aws iam delete-user-policy --user-name "$VICTIM" --policy-name "$pol"
    say "deleted inline policy $pol"
  done
  for arn in $(aws iam list-attached-user-policies --user-name "$VICTIM" \
                 --query 'AttachedPolicies[].PolicyArn' --output text); do
    aws iam detach-user-policy --user-name "$VICTIM" --policy-arn "$arn"
    say "detached $arn"
  done
  for grp in $(aws iam list-groups-for-user --user-name "$VICTIM" --query 'Groups[].GroupName' --output text); do
    aws iam remove-user-from-group --user-name "$VICTIM" --group-name "$grp"
    say "removed from group $grp"
  done
  aws iam delete-login-profile --user-name "$VICTIM" 2>/dev/null && say "deleted login profile" || true
  aws iam delete-user --user-name "$VICTIM"
  say "deleted user $VICTIM"
  printf '\n  Done.\n\n'
  exit 0
fi

# ======================================================== preflight

CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

head_ "Preflight"
say "profile : ${AWS_PROFILE:-<default>}"
say "caller  : $(printf '%s' "$CALLER_ARN" | redact)"
say "account : <ACCOUNT_ID>"

has_user=0;   user_exists "$BASE_USER" && has_user=1
has_key=0
[ "$has_user" = 1 ] && [ -n "$(aws iam list-access-keys --user-name "$BASE_USER" \
    --query 'AccessKeyMetadata[].AccessKeyId' --output text)" ] && has_key=1
has_mfa=0
[ "$has_user" = 1 ] && aws iam list-mfa-devices --user-name "$BASE_USER" \
    --query 'MFADevices[0].SerialNumber' --output text 2>/dev/null | grep -qv '^None$' && has_mfa=1
has_role=0;   aws iam get-role --role-name "$ADMIN_ROLE" >/dev/null 2>&1 && has_role=1

head_ "Plan"
[ "$has_user" = 1 ] && say "skip   user $BASE_USER (exists)"          || say "create user $BASE_USER"
[ "$has_key"  = 1 ] && say "skip   access key (user already has one)" || say "create access key, write [$SRC_PROFILE] to credentials file"
[ "$has_mfa"  = 1 ] && say "skip   MFA device (already enabled)"      || say "create and enable MFA device '$MFA_NAME'"
[ "$has_role" = 1 ] && say "update trust policy of $ADMIN_ROLE"       || say "create role $ADMIN_ROLE (MFA required, 1 hour sessions)"
say "ensure AdministratorAccess attached to $ADMIN_ROLE"
say "write  inline policy $INLINE_POLICY_NAME on $BASE_USER (assume only)"
say "write  profile [$PROFILE] to ~/.aws/config"
say ""
say "Not touched: root, other users, their permissions."

if [ "$CHECK_ONLY" = 1 ]; then
  printf '\n  --check: nothing was changed.\n\n'
  exit 0
fi

printf '\n  Proceed? [y/N] '
read -r reply
case "$reply" in y|Y) ;; *) printf '\n  Aborted.\n\n'; exit 1 ;; esac

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
umask 077

# ======================================================== 1. user + key

head_ "1. User and access key"

if [ "$has_user" = 0 ]; then
  aws iam create-user --user-name "$BASE_USER" >/dev/null
  say "created user $BASE_USER"
else
  say "user exists, skipping"
fi

if [ "$has_key" = 0 ]; then
  aws iam create-access-key --user-name "$BASE_USER" > "$WORK/key.json"
  CREDS_FILE="$CREDS_FILE" SRC_PROFILE="$SRC_PROFILE" REGION="$REGION" \
  python3 - "$WORK/key.json" <<'PY'
import json, os, pathlib, sys

key = json.load(open(sys.argv[1]))["AccessKey"]
path = pathlib.Path(os.environ["CREDS_FILE"]).expanduser()
profile = os.environ["SRC_PROFILE"]
path.parent.mkdir(parents=True, exist_ok=True)
text = path.read_text() if path.exists() else ""
if f"[{profile}]" in text:
    raise SystemExit(f"profile [{profile}] already present in {path}; refusing to overwrite")
block = (
    f"\n[{profile}]\n"
    f"aws_access_key_id = {key['AccessKeyId']}\n"
    f"aws_secret_access_key = {key['SecretAccessKey']}\n"
)
with open(path, "a") as fh:
    fh.write(block)
path.chmod(0o600)
print(f"  wrote [{profile}] to {path}")
PY
  aws configure set region "$REGION" --profile "$SRC_PROFILE"
  say "secret never passed through argv or shell history"
else
  say "access key exists, skipping"
fi

# ======================================================== 2. MFA

head_ "2. MFA device"

if [ "$has_mfa" = 1 ]; then
  say "already enabled, skipping"
else
  stale=0
  aws iam list-virtual-mfa-devices --assignment-status Unassigned \
      --query 'VirtualMFADevices[].SerialNumber' --output text | grep -qF "$MFA_SERIAL" && stale=1

  if [ "$stale" = 1 ]; then
    # A device created by an earlier run that never got enabled. Its seed only
    # exists inside the authenticator app that scanned the QR; the PNG is gone.
    say "A virtual MFA device named '$MFA_NAME' exists but was never enabled."
    say "That happens when a previous run was interrupted."
    say ""
    say "Is it already in your authenticator app (did you scan that QR)?"
    printf '  [y] enter codes from it   [n] delete it and issue a new QR : '
    read -r reuse
    case "$reuse" in
      y|Y) say "reusing $MFA_NAME" ;;
      *)   aws iam delete-virtual-mfa-device --serial-number "$MFA_SERIAL"
           say "deleted the unusable device"
           aws iam create-virtual-mfa-device --virtual-mfa-device-name "$MFA_NAME" \
             --outfile "$WORK/qr.png" --bootstrap-method QRCodePNG >/dev/null
           say "created a fresh virtual device $MFA_NAME"
           say "opening the QR code; scan it with your authenticator app"
           say "the PNG holds the seed and is deleted when this script exits"
           open "$WORK/qr.png" 2>/dev/null || say "open failed, view: $WORK/qr.png"
           say ""
           say "Scan it now, before continuing."
           printf '  press Enter when scanned: '
           read -r _ ;;
    esac
  else
    aws iam create-virtual-mfa-device --virtual-mfa-device-name "$MFA_NAME" \
      --outfile "$WORK/qr.png" --bootstrap-method QRCodePNG >/dev/null
    say "created virtual device $MFA_NAME"
    say "opening the QR code; scan it with your authenticator app"
    say "the PNG holds the seed and is deleted when this script exits"
    open "$WORK/qr.png" 2>/dev/null || say "open failed, view: $WORK/qr.png"
  fi
  say ""
  say "AWS needs two CONSECUTIVE codes, not the same one twice."
  say "Type the code showing now, wait for it to roll over, then type the next one."
  for attempt in 1 2 3; do
    printf '\n  code 1               : '; read -r C1
    printf '  code 2 (after it rolls): '; read -r C2
    if aws iam enable-mfa-device --user-name "$BASE_USER" --serial-number "$MFA_SERIAL" \
         --authentication-code1 "$C1" --authentication-code2 "$C2" 2>/dev/null; then
      unset C1 C2
      say "MFA enabled on $BASE_USER"
      break
    fi
    unset C1 C2
    if [ "$attempt" = 3 ]; then
      die "The codes were rejected three times. Check that the entry in your app is named '$MFA_NAME' and that the two codes are consecutive."
    fi
    say "rejected; the codes must be current and consecutive. Try again."
  done
fi

# ======================================================== 3. role

head_ "3. Admin role"

cat > "$WORK/trust.json" <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "AWS": "arn:aws:iam::${ACCOUNT_ID}:user/${BASE_USER}" },
      "Action": "sts:AssumeRole",
      "Condition": {
        "Bool": { "aws:MultiFactorAuthPresent": "true" },
        "NumericLessThan": { "aws:MultiFactorAuthAge": "3600" }
      }
    }
  ]
}
JSON

if [ "$has_role" = 1 ]; then
  aws iam update-assume-role-policy --role-name "$ADMIN_ROLE" \
    --policy-document "file://$WORK/trust.json"
  say "trust policy updated"
else
  aws iam create-role --role-name "$ADMIN_ROLE" \
    --description "Administrative access for Terraform. Assumable only with MFA." \
    --assume-role-policy-document "file://$WORK/trust.json" \
    --max-session-duration 3600 >/dev/null
  say "role created"
fi
aws iam attach-role-policy --role-name "$ADMIN_ROLE" --policy-arn "$ADMIN_POLICY_ARN"
say "AdministratorAccess attached"

# ======================================================== 4. assume policy

head_ "4. Assume permission"

cat > "$WORK/assume.json" <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow", "Action": "sts:AssumeRole", "Resource": "${ROLE_ARN}" }
  ]
}
JSON
aws iam put-user-policy --user-name "$BASE_USER" \
  --policy-name "$INLINE_POLICY_NAME" --policy-document "file://$WORK/assume.json"
say "$BASE_USER may assume $ADMIN_ROLE and nothing else"

# ======================================================== 5. profile

head_ "5. Profile [$PROFILE]"
aws configure set source_profile   "$SRC_PROFILE" --profile "$PROFILE"
aws configure set role_arn         "$ROLE_ARN"    --profile "$PROFILE"
aws configure set mfa_serial       "$MFA_SERIAL"  --profile "$PROFILE"
aws configure set duration_seconds 3600           --profile "$PROFILE"
aws configure set region           "$REGION"      --profile "$PROFILE"
say "written to ~/.aws/config (no secrets there)"

# ======================================================== 6. verify

head_ "6. Verify"
say "Assuming the role. You will be asked for an MFA code."
say "New IAM principals take a few seconds to propagate; this retries."
say ""
for attempt in 1 2 3 4 5; do
  if NEW_ARN="$(aws sts get-caller-identity --profile "$PROFILE" --query Arn --output text 2>/dev/null)"; then
    say "caller is now $(printf '%s' "$NEW_ARN" | redact)"
    cat <<EOT

  Done. Next steps, in this order:

    1. set AWS_PROFILE_ADMIN=$PROFILE in .env
    2. retire the unused admin:  bash $0 --retire terraform-admin
    3. delete the root access key: infra/iam/README.md section 11.5
    4. remove [default] from ~/.aws/credentials and ~/.aws/config

EOT
    exit 0
  fi
  say "attempt $attempt failed, waiting 5s"
  sleep 5
done
die "Could not assume $ADMIN_ROLE. Nothing was rolled back; fix the error and re-run."
