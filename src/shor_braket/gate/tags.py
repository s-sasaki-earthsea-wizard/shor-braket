# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

"""The tags attached to every quantum task.

Tags here carry accounting and audit information. **They carry no authorization.** An earlier
design used ``campaign=device-comparison`` as the key that opened an IAM deny on AQT, but
``aws:RequestTag`` is a value the caller puts on its own request, so the role could lift its own
deny. Capability now lives in the role split instead, and the tags went back to what tags are
good at (ADR-0004).

* ``project`` is constant and exists so the cost-allocation report can separate this work from
  everything else in the account.
* ``oracle`` records how much of the answer was built into the circuit. CLAUDE.md forbids
  quietly using a compiled oracle, and putting the mode in the billing record means that claim
  is checkable from outside the repository.
* ``campaign`` is optional and free-form, for grouping the spend of one experiment.

Cost allocation tags only apply to data recorded after they are activated in the billing
console, so all three have to be activated before the first task (issue #4).
"""

from __future__ import annotations

import re

PROJECT_TAG_VALUE = "shor-braket"
TAG_KEY_PROJECT = "project"
TAG_KEY_ORACLE = "oracle"
TAG_KEY_CAMPAIGN = "campaign"

MAX_TAG_VALUE_LENGTH = 256
# AWS accepts letters, numbers, spaces and + - = . _ : / @ in a tag value.
TAG_VALUE_PATTERN = re.compile(r"^[\w\s+\-=._:/@]*$", re.UNICODE)


def validate_tag_value(key: str, value: str) -> None:
    """Check one tag value against the characters and length AWS accepts.

    Args:
        key: Tag key, used only in the error message.
        value: Tag value to check.

    Raises:
        ValueError: If the value is too long or holds a character AWS rejects. Failing here beats
            having ``CreateQuantumTask`` reject the request after the preflight said yes.
    """
    if len(value) > MAX_TAG_VALUE_LENGTH:
        raise ValueError(
            f"tag {key!r} is {len(value)} characters; AWS allows {MAX_TAG_VALUE_LENGTH}"
        )
    if not TAG_VALUE_PATTERN.match(value):
        raise ValueError(
            f"tag {key!r} has characters AWS rejects: {value!r}. "
            "Allowed: letters, numbers, spaces and + - = . _ : / @"
        )


def build_tags(*, oracle_mode: str, campaign: str | None = None) -> dict[str, str]:
    """Build the tag set for one quantum task.

    Args:
        oracle_mode: The oracle the circuit uses, recorded so the amount of built-in knowledge
            survives in the billing record.
        campaign: Optional experiment grouping. Blank and ``None`` both mean no campaign tag;
            an absent tag is cleaner in a cost report than an empty one.

    Returns:
        The tags to attach, ordered project, oracle, campaign.

    Raises:
        ValueError: If ``oracle_mode`` is empty, or any value is not a legal AWS tag value.
    """
    if not oracle_mode.strip():
        raise ValueError("oracle_mode must not be empty; the oracle is always recorded")

    tags = {TAG_KEY_PROJECT: PROJECT_TAG_VALUE, TAG_KEY_ORACLE: oracle_mode.strip()}
    if campaign and campaign.strip():
        tags[TAG_KEY_CAMPAIGN] = campaign.strip()

    for key, value in tags.items():
        validate_tag_value(key, value)
    return tags
