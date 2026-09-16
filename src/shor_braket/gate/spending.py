# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

"""Read the Amazon Braket spending limit of a device.

A spending limit is the only stop that lives on the service side: Braket itself refuses
``CreateQuantumTask`` when the task's estimate would push cumulative spend on that device past
the limit. The client-side preflight reads the same number so the operator sees, before
confirming, what the service is going to compare against.

The lookup is a plain callable so that the gate can be exercised without AWS. Tests pass a stub;
:func:`aws_spending_limit_lookup` is the real one and is only built when a submission is actually
attempted. It needs ``braket:SearchSpendingLimits``, which the read-only and execute policies
grant (issue #3).

Field names follow the ``SearchSpendingLimits`` response as botocore models it: money arrives as
strings (``spendingLimit`` / ``totalSpend`` / ``queuedSpend``, at most two decimals) and the
optional ``timePeriod`` carries ``startAt`` / ``endAt`` as timezone-aware ``datetime`` objects.
Timestamps are normalized to ISO 8601 strings here so the preflight report stays JSON-serializable.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

UNAVAILABLE = "unavailable"


class BraketSpendingClient(Protocol):
    """The one call this module needs from a boto3 Braket client."""

    def search_spending_limits(self, **kwargs: object) -> Mapping[str, Any]:
        """Return the account's spending limits, optionally filtered and paginated."""
        ...


def _moment(value: object) -> datetime:
    """Coerce an API timestamp or an ISO 8601 string to a timezone-aware UTC ``datetime``.

    Args:
        value: A ``datetime`` (what botocore returns for ``startAt`` / ``endAt``), an epoch
            number, or an ISO 8601 string. A naive value is taken as UTC.

    Returns:
        The same instant as an aware ``datetime`` in UTC.
    """
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, int | float):
        moment = datetime.fromtimestamp(value, tz=UTC)
    else:
        moment = datetime.fromisoformat(str(value))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _iso_utc(value: object) -> str | None:
    """Normalize an optional API timestamp to an ISO 8601 UTC string, or ``None``."""
    return None if value is None else _moment(value).isoformat()


@dataclass(frozen=True)
class SpendingLimitStatus:
    """The service-side budget for one device.

    ``current_spend_usd`` is the API's ``totalSpend``: what the device has already consumed in
    the current period. ``active_from`` / ``active_to`` are ISO 8601 strings; ``None`` means the
    limit has no period and is always in force.
    """

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
            now: Point in time to test; defaults to the current UTC time. A naive value is
                taken as UTC.

        Returns:
            ``True`` when the limit is in force. A limit with no period is always in force.
        """
        when = _moment(now) if now is not None else datetime.now(UTC)
        if self.active_from and when < _moment(self.active_from):
            return False
        return not (self.active_to and when > _moment(self.active_to))

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


def spending_limit_from_api(payload: Mapping[str, Any]) -> SpendingLimitStatus:
    """Convert one ``SearchSpendingLimits`` entry into a status.

    Args:
        payload: One element of the API's ``spendingLimits`` list. Money fields are strings,
            ``timePeriod.startAt`` / ``endAt`` are ``datetime`` objects when present.

    Returns:
        The status, with all money as ``Decimal`` and timestamps as ISO 8601 UTC strings.
    """
    period = payload.get("timePeriod") or {}
    return SpendingLimitStatus(
        device_arn=str(payload.get("deviceArn", "")),
        limit_usd=_decimal(payload.get("spendingLimit")),
        current_spend_usd=_decimal(payload.get("totalSpend")),
        queued_spend_usd=_decimal(payload.get("queuedSpend")),
        active_from=_iso_utc(period.get("startAt")),
        active_to=_iso_utc(period.get("endAt")),
    )


def aws_spending_limit_lookup(client: BraketSpendingClient) -> SpendingLimitLookup:
    """Build a lookup backed by a boto3 Braket client.

    Args:
        client: A ``boto3`` Braket client, already bound to the device's region and to a
            principal that holds ``braket:SearchSpendingLimits``.

    Returns:
        A callable that returns the limit for a device ARN, or ``None`` when the device has none.
        The request is filtered by ``deviceArn`` (the only filter the API supports) and follows
        ``nextToken`` pages; the device ARN is still checked on every entry rather than trusted.
    """

    def lookup(device_arn: str) -> SpendingLimitStatus | None:
        kwargs: dict[str, object] = {
            "filters": [{"name": "deviceArn", "values": [device_arn], "operator": "EQUAL"}]
        }
        while True:
            response = client.search_spending_limits(**kwargs)
            for entry in response.get("spendingLimits", []):
                if entry.get("deviceArn") == device_arn:
                    return spending_limit_from_api(entry)
            token = response.get("nextToken")
            if not token:
                return None
            kwargs["nextToken"] = token

    return lookup
