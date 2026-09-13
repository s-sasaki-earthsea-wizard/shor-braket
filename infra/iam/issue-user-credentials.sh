#!/usr/bin/env bash
#
# Issue a long-term access key for one of the project's IAM users and write it
# straight into the shared credentials file, then optionally register an MFA
# device. Run this after `make tf-apply` has created the users.
#
# The secret never appears in argv, shell history, or terminal scrollback:
# `aws iam create-access-key` writes JSON to a file inside a mktemp directory,
# python3 copies the value into ~/.aws/credentials, and the directory is removed
# on exit. Do not replace this with `aws configure set`, whose argument list is
# visible to any local `ps`.
#
# Usage:
#   bash infra/iam/issue-user-credentials.sh <iam-user> <profile> [--mfa]
#
# Examples:
#   ... shor-braket-operator shor-braket-ro --mfa    # operator needs MFA to assume
#   ... shor-braket-monitor  shor-braket-monitor     # read-only, MFA optional
#
# Environment:
#   ADMIN_PROFILE  profile with IAM rights (default: admin)
#   REGION         region recorded in the new profile (default: eu-north-1)

set -euo pipefail

IAM_USER="${1:-}"
PROFILE="${2:-}"
WANT_MFA=0
[ "${3:-}" = "--mfa" ] && WANT_MFA=1

ADMIN_PROFILE="${ADMIN_PROFILE:-admin}"
REGION="${REGION:-eu-north-1}"
CREDS_FILE="${AWS_SHARED_CREDENTIALS_FILE:-$HOME/.aws/credentials}"

say()   { printf '  %s\n' "$*"; }
head_() { printf '\n== %s\n' "$*"; }
die()   { printf '\n  ERROR: %s\n\n' "$*" >&2; exit 1; }

[ -n "$IAM_USER" ] && [ -n "$PROFILE" ] \
  || die "usage: $0 <iam-user> <profile> [--mfa]"
command -v python3 >/dev/null || die "python3 not found."

export AWS_PROFILE="$ADMIN_PROFILE"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text 2>/dev/null)" \
  || die "cannot authenticate with profile '$ADMIN_PROFILE'. Try: aws sts get-caller-identity --profile $ADMIN_PROFILE"
redact() { sed -e "s/${ACCOUNT_ID}/<ACCOUNT_ID>/g" -e 's/AKIA[A-Z0-9]\{16\}/<AKID>/g'; }

aws iam get-user --user-name "$IAM_USER" >/dev/null 2>&1 \
  || die "IAM user '$IAM_USER' does not exist. Run 'make tf-apply' first."

head_ "Issue credentials for $IAM_USER -> profile [$PROFILE]"
say "admin profile : $ADMIN_PROFILE"
say "region        : $REGION"
say "MFA           : $([ "$WANT_MFA" = 1 ] && echo "register" || echo "skip")"

# The key step and the MFA step are independent, so an interrupted run can be
# resumed: whatever already exists is skipped rather than treated as an error.
have_profile=0
grep -qE "^\[$PROFILE\]" "$CREDS_FILE" 2>/dev/null && have_profile=1
existing="$(aws iam list-access-keys --user-name "$IAM_USER" --query 'AccessKeyMetadata[].AccessKeyId' --output text)"

need_key=1
if [ "$have_profile" = 1 ] && [ -n "$existing" ]; then
  need_key=0
  say ""
  say "profile [$PROFILE] and a key for $IAM_USER both exist; skipping the key step"
elif [ "$have_profile" = 1 ]; then
  die "profile [$PROFILE] exists in $CREDS_FILE but $IAM_USER has no access key.
  The profile holds a stale or foreign key. Remove that block, then re-run."
elif [ -n "$existing" ]; then
  say ""
  say "This user already has a key: $(printf '%s' "$existing" | redact)"
  say "IAM allows two per user; a third request fails."
  printf '  Issue another anyway? [y/N] '
  read -r ans
  case "$ans" in y|Y) ;; *) printf '\n  Aborted.\n\n'; exit 1 ;; esac
fi

if [ "$need_key" = 0 ] && [ "$WANT_MFA" = 0 ]; then
  printf '\n  Nothing to do. Pass --mfa (make issue-creds ... MFA=1) to register a device.\n\n'
  exit 0
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
umask 077

head_ "1. Access key"
if [ "$need_key" = 0 ]; then
  say "already issued, skipping"
else
aws iam create-access-key --user-name "$IAM_USER" > "$WORK/key.json"
CREDS_FILE="$CREDS_FILE" PROFILE="$PROFILE" python3 - "$WORK/key.json" <<'PY'
import json, os, pathlib, sys

key = json.load(open(sys.argv[1]))["AccessKey"]
path = pathlib.Path(os.environ["CREDS_FILE"]).expanduser()
profile = os.environ["PROFILE"]
path.parent.mkdir(parents=True, exist_ok=True)
text = path.read_text() if path.exists() else ""
if f"[{profile}]" in text:
    raise SystemExit(f"profile [{profile}] appeared mid-run; refusing to overwrite")
with open(path, "a") as fh:
    fh.write(
        f"\n[{profile}]\n"
        f"aws_access_key_id = {key['AccessKeyId']}\n"
        f"aws_secret_access_key = {key['SecretAccessKey']}\n"
    )
path.chmod(0o600)
print(f"  wrote [{profile}] to {path} (mode 600)")
PY
aws configure set region "$REGION" --profile "$PROFILE"
say "secret never passed through argv or shell history"
fi

if [ "$WANT_MFA" = 1 ]; then
  head_ "2. MFA device"
  if aws iam list-mfa-devices --user-name "$IAM_USER" \
       --query 'MFADevices[0].SerialNumber' --output text 2>/dev/null | grep -qv '^None$'; then
    say "already enabled, skipping"
  else
    MFA_SERIAL="arn:aws:iam::${ACCOUNT_ID}:mfa/${IAM_USER}"
    if aws iam list-virtual-mfa-devices --assignment-status Unassigned \
         --query 'VirtualMFADevices[].SerialNumber' --output text | grep -qF "$MFA_SERIAL"; then
      say "a device named $IAM_USER exists but was never enabled"
      while true; do
        printf '  [y] it is in my authenticator   [n] delete it and issue a new QR : '
        read -r reuse
        case "$reuse" in y|Y|n|N) break ;; *) say "please answer y or n" ;; esac
      done
      case "$reuse" in
        y|Y) : ;;
        *)   aws iam delete-virtual-mfa-device --serial-number "$MFA_SERIAL"
             aws iam create-virtual-mfa-device --virtual-mfa-device-name "$IAM_USER" \
               --outfile "$WORK/qr.png" --bootstrap-method QRCodePNG >/dev/null
             open "$WORK/qr.png" 2>/dev/null || say "view: $WORK/qr.png"
             printf '  press Enter when scanned: '; read -r _ ;;
      esac
    else
      aws iam create-virtual-mfa-device --virtual-mfa-device-name "$IAM_USER" \
        --outfile "$WORK/qr.png" --bootstrap-method QRCodePNG >/dev/null
      say "opening the QR code; the PNG holds the seed and is deleted on exit"
      open "$WORK/qr.png" 2>/dev/null || say "view: $WORK/qr.png"
      printf '  press Enter when scanned: '; read -r _
    fi

    say ""
    say "AWS needs two CONSECUTIVE codes, not the same one twice."
    for attempt in 1 2 3; do
      printf '\n  code 1               : '; read -r C1
      printf '  code 2 (after it rolls): '; read -r C2
      if aws iam enable-mfa-device --user-name "$IAM_USER" --serial-number "$MFA_SERIAL" \
           --authentication-code1 "$C1" --authentication-code2 "$C2" 2>/dev/null; then
        unset C1 C2; say "MFA enabled on $IAM_USER"; break
      fi
      unset C1 C2
      [ "$attempt" = 3 ] && die "codes rejected three times."
      say "rejected; codes must be current and consecutive."
    done
  fi
fi

head_ "Done"
say "verify: aws sts get-caller-identity --profile $PROFILE"
say ""
say "For the operator, add the assume profile to ~/.aws/config with:"
say "  terraform -chdir=infra/terraform output -raw aws_config_snippet"
printf '\n'
