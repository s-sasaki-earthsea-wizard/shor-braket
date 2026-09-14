# Copyright 2026 Syota Sasaki (Earthsea Wizard)
# SPDX-License-Identifier: Apache-2.0

"""Validated records and the guard that stands between a circuit and a paid QPU task.

The three modules are layered: :mod:`circuit_hash` decides what "the same program" means,
:mod:`record` stores the proof that one such program passed local validation, and
:mod:`preflight` refuses everything that cannot show one. Import from the submodules directly.
"""
