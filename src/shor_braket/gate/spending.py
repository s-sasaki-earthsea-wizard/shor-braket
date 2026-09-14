# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

"""Read the Amazon Braket spending limit of a device.

A spending limit is the only stop that lives on the service side: Braket itself refuses
``CreateQuantumTask`` when the task's estimate would push cumulative spend on that device past
the limit. The client-side preflight reads the same number so the operator sees, before
confirming, what the service is going to compare against.

The lookup is a plain callable so that the gate can be exercised without AWS. Tests pass a stub;
:func:`aws_spending_limit_lookup` is the real one and is only built when a submission is actually
attempted. It needs ``braket:SearchSpendingLimits``, which the read-only policy does not grant
yet (issue #3).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

UNAVAILABLE = "unavailable"


class BraketSpendingClient(Protocol):
    """The one call this module needs from a boto3 Braket client."""

    def search_spending_limits(
        self, *, filters: Sequence[Mapping[str, object]]
    ) -> Mapping[str, Any]:
        """Return the account's spending limits."""
        ...


@dataclass(frozen=True)
class SpendingLimitStatus:
    """The service-side budget for one device."""

    device_arn: str
    limit_usd: Decimal
    current_spend_usd: Decimal
    queued_spend_usd: Decimal
    active_from: str | None = None
    active_to: str | None = None

    @property
    def remaining_usd(self) -> Decimal:
        """Return what is left after current and queued spend, never below zero."""
        remaining = self.limit_usd - self.current_spend_usd - self.queued_spend_usd
        return remaining if remaining > 0 else Decimal("0")

    def is_active(self, now: datetime | None = None) -> bool:
        """Report whether the limit's time period covers this moment.

        Args:
            now: Point in time to test; defaults to the current UTC time.

        Returns:
            ``True`` when the limit is in force. A limit with no period is always in force.
        """
        when = now or datetime.now(UTC)
        if self.active_from and when < datetime.fromisoformat(self.active_from):
            return False
        return not (self.active_to and when > datetime.fromisoformat(self.active_to))

    def to_dict(self) -> dict[str, Any]:
        """Serialize the status with the money as strings."""
        return {
            "device_arn": self.device_arn,
            "limit_usd": str(self.limit_usd),
            "current_spend_usd": str(self.current_spend_usd),
            "queued_spend_usd": str(self.queued_spend_usd),
            "remaining_usd": str(self.remaining_usd),
            "active_from": self.active_from,
            "active_to": self.active_to,
        }


SpendingLimitLookup = Callable[[str], SpendingLimitStatus | None]


def no_spending_limit_lookup(device_arn: str) -> SpendingLimitStatus | None:
    """Report no knowledge of any spending limit.

    Args:
        device_arn: Ignored.

    Returns:
        ``None`` always. This is the default for offline runs: the preflight then reports the
        spending limit as unknown and refuses to treat it as headroom.
    """
    del device_arn
    return None


def _decimal(value: object) -> Decimal:
    """Coerce an API number to ``Decimal`` via ``str`` so binary floats do not creep in."""
    return Decimal(str(value)) if value is not None else Decimal("0")


def spending_limit_from_api(payload: dict[str, Any]) -> SpendingLimitStatus:
    """Convert one ``SearchSpendingLimits`` entry into a status.

    Args:
        payload: One element of the API's ``spendingLimits`` list.

    Returns:
        The status, with all money as ``Decimal``.
    """
    period = payload.get("timePeriod") or {}
    return SpendingLimitStatus(
        device_arn=str(payload.get("deviceArn", "")),
        limit_usd=_decimal(payload.get("spendingLimit")),
        current_spend_usd=_decimal(payload.get("currentSpend")),
        queued_spend_usd=_decimal(payload.get("queuedSpend")),
        active_from=period.get("start"),
        active_to=period.get("end"),
    )


def aws_spending_limit_lookup(client: BraketSpendingClient) -> SpendingLimitLookup:
    """Build a lookup backed by a boto3 Braket client.

    Args:
        client: A ``boto3`` Braket client, already bound to the device's region and to a
            principal that holds ``braket:SearchSpendingLimits``.

    Returns:
        A callable that returns the limit for a device ARN, or ``None`` when the device has none.
    """

    def lookup(device_arn: str) -> SpendingLimitStatus | None:
        response = client.search_spending_limits(filters=[])
        for entry in response.get("spendingLimits", []):
            if entry.get("deviceArn") == device_arn:
                return spending_limit_from_api(entry)
        return None

    return lookup
