from __future__ import annotations

import pytest

from artifactfit_mm.stages.machine import BlockingState, Stage, StageMachine


def test_stage_machine_requires_single_step_progression() -> None:
    machine = StageMachine()
    machine.pass_stage(Stage.P0_DISCOVERED)
    with pytest.raises(ValueError, match="non-monotone"):
        machine.pass_stage(Stage.P2_ENVIRONMENT_READY)
    machine.pass_stage(Stage.P1_POLICY_ACCEPTED)
    assert machine.highest_stage is Stage.P1_POLICY_ACCEPTED


def test_blocking_state_is_terminal() -> None:
    machine = StageMachine()
    machine.pass_stage(Stage.P0_DISCOVERED)
    machine.block(BlockingState.BLOCKED_LICENSE, "missing")
    assert machine.terminal
    assert machine.expected_next() is None
    with pytest.raises(ValueError):
        machine.pass_stage(Stage.P1_POLICY_ACCEPTED)
