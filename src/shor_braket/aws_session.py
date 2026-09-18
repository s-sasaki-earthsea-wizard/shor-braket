# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

"""The AWS sessions this project is allowed to build, and which one each device needs.

Everything that costs money runs under one of two roles, and which one is not a preference: the
execution role denies AQT unconditionally and the AQT role denies IQM unconditionally (ADR-0004).
Picking the wrong one is an ``AccessDenied``, not an expensive mistake, which is the point of the
split. This module maps a logical device name to the profile that can reach it so that no caller
has to remember the rule.

Reading is separate again. Task status, spending limits and results only need the read-only
profile, which has no assume-role step and therefore no MFA prompt. Read paths use
:func:`readonly_session` so that checking on a task never asks for a code.

Nothing here talks to AWS on import. A :class:`boto3.Session` is cheap; the assume-role call, and
with it the MFA prompt, happens on the first API call made through it.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - import only for type checking
    import boto3

# Devices reached through ShorBraketAqtRole. Everything else goes through the execution role,
# which denies these unconditionally.
AQT_DEVICE_KEYS = frozenset({"ibex"})

PROFILE_ENV_EXEC = "AWS_PROFILE_EXEC"
PROFILE_ENV_AQT = "AWS_PROFILE_AQT"
PROFILE_ENV_RO = "AWS_PROFILE_RO"
REGION_ENV = "AWS_REGION"
RESULTS_BUCKET_ENV = "BRAKET_RESULTS_BUCKET"


class AwsConfigurationError(RuntimeError):
    """Raised when the environment does not say which credentials or region to use."""


def _require_env(name: str, purpose: str) -> str:
    """Read one required environment variable.

    Args:
        name: Variable to read.
        purpose: What it is needed for, used in the error message.

    Returns:
        The value, stripped.

    Raises:
        AwsConfigurationError: If the variable is unset or blank.
    """
    value = os.environ.get(name, "").strip()
    if not value:
        raise AwsConfigurationError(
            f"{name} is not set; it is needed for {purpose}. "
            "Copy .env.example to .env and fill it in, then use a make target so the file is "
            "loaded into the environment."
        )
    return value


def submission_profile_env(device_key: str) -> str:
    """Return the environment variable naming the profile that may submit to this device.

    Args:
        device_key: Logical device name.

    Returns:
        ``AWS_PROFILE_AQT`` for AQT hardware, ``AWS_PROFILE_EXEC`` otherwise.
    """
    return PROFILE_ENV_AQT if device_key in AQT_DEVICE_KEYS else PROFILE_ENV_EXEC


def region() -> str:
    """Return the region every device and the results bucket live in.

    Returns:
        The configured region.

    Raises:
        AwsConfigurationError: If the region is not configured.
    """
    return _require_env(REGION_ENV, "choosing the Braket endpoint and the results bucket region")


def results_bucket() -> str:
    """Return the bucket Braket writes task results to.

    Returns:
        The bucket name.

    Raises:
        AwsConfigurationError: If the bucket is not configured.
    """
    return _require_env(RESULTS_BUCKET_ENV, "telling Braket where to write task results")


def _session(profile_name: str) -> boto3.Session:
    """Build a session for one profile in the project's region.

    Args:
        profile_name: Profile from the AWS config file.

    Returns:
        The session. No AWS call has been made yet.

    Raises:
        AwsConfigurationError: If boto3 cannot be imported or the profile does not exist.
    """
    try:
        import boto3
        from botocore.exceptions import ProfileNotFound
    except ImportError as error:  # pragma: no cover - boto3 is a hard dependency
        raise AwsConfigurationError(f"boto3 is not importable: {error}") from error
    try:
        return boto3.Session(profile_name=profile_name, region_name=region())
    except ProfileNotFound as error:
        raise AwsConfigurationError(
            f"profile {profile_name!r} is not in the AWS config file. Inside the container the "
            "config is read from /aws, which is the host's ~/.aws mounted read-only."
        ) from error


def submission_session(device_key: str) -> boto3.Session:
    """Build the session allowed to create tasks on this device.

    Args:
        device_key: Logical device name.

    Returns:
        A session for the execution role, or the AQT role for AQT hardware.

    Raises:
        AwsConfigurationError: If the profile for that role is not configured.
    """
    env_name = submission_profile_env(device_key)
    purpose = f"submitting a task to {device_key}"
    return _session(_require_env(env_name, purpose))


def readonly_session() -> boto3.Session:
    """Build the session used for every free, read-only call.

    Returns:
        A session for the read-only profile, which needs no assume-role step and so never
        prompts for an MFA code.

    Raises:
        AwsConfigurationError: If the read-only profile is not configured.
    """
    return _session(_require_env(PROFILE_ENV_RO, "reading task status and spending limits"))


def caller_identity(session: boto3.Session) -> dict[str, str]:
    """Ask STS who the session is, so the audit record names the principal that paid.

    This is the first call made through a submission session, which means it is where the MFA
    prompt appears. Doing it before the preflight's spending-limit read keeps the prompt at a
    predictable point in the output.

    Args:
        session: The session to identify.

    Returns:
        The account, principal ARN and user id.
    """
    response: dict[str, Any] = session.client("sts").get_caller_identity()
    return {
        "account": str(response.get("Account", "")),
        "arn": str(response.get("Arn", "")),
        "user_id": str(response.get("UserId", "")),
    }
