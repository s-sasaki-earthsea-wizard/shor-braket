# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path

import pytest

from shor_braket.devices import DEFAULT_SNAPSHOT_DIR, load_snapshot

SNAPSHOT_DIR = Path(__file__).resolve().parents[1] / DEFAULT_SNAPSHOT_DIR


def _load_or_skip(key: str):
    try:
        return load_snapshot(key, SNAPSHOT_DIR)
    except FileNotFoundError:
        pytest.skip(f"no committed snapshot for {key}; run `make device-snapshot`")


@pytest.fixture
def snapshot_dir() -> Path:
    return SNAPSHOT_DIR


@pytest.fixture
def garnet_snapshot():
    return _load_or_skip("garnet")


@pytest.fixture
def ibex_snapshot():
    return _load_or_skip("ibex")
