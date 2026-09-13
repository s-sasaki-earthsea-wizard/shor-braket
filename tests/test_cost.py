# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

import pytest

from shor_braket.cost import qpu_cost_estimates


def test_qpu_costs_are_limited_to_approved_devices():
    estimates = qpu_cost_estimates(1000)

    assert [estimate["device"] for estimate in estimates] == ["garnet", "emerald", "ibex"]
    assert [estimate["estimated_cost_usd"] for estimate in estimates] == [
        "1.75000",
        "1.90000",
        "23.80000",
    ]
    assert all(estimate["shots_valid"] for estimate in estimates)


def test_ibex_shot_limit_is_reported():
    estimates = {item["device"]: item for item in qpu_cost_estimates(3000)}

    assert estimates["garnet"]["shots_valid"] is True
    assert estimates["ibex"]["shots_valid"] is False


def test_nonpositive_cost_inputs_are_rejected():
    with pytest.raises(ValueError, match="shots must be positive"):
        qpu_cost_estimates(0)
