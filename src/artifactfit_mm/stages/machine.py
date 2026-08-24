from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Stage(StrEnum):
    P0_DISCOVERED = "P0_DISCOVERED"
    P1_POLICY_ACCEPTED = "P1_POLICY_ACCEPTED"
    P2_ENVIRONMENT_READY = "P2_ENVIRONMENT_READY"
    P3_IMPORT_PASS = "P3_IMPORT_PASS"
    P4_ENTRYPOINT_PASS = "P4_ENTRYPOINT_PASS"
    P5_DEMO_PASS = "P5_DEMO_PASS"
    P6_FORWARD_PASS = "P6_FORWARD_PASS"
    P7_BACKWARD_SMOKE_PASS = "P7_BACKWARD_SMOKE_PASS"
    P8_SHORT_OPTIMIZATION_PASS = "P8_SHORT_OPTIMIZATION_PASS"


STAGE_ORDER = tuple(Stage)


class BlockingState(StrEnum):
    BLOCKED_LICENSE = "BLOCKED_LICENSE"
    BLOCKED_DATA = "BLOCKED_DATA"
    BLOCKED_WEIGHTS = "BLOCKED_WEIGHTS"
    BLOCKED_ENVIRONMENT = "BLOCKED_ENVIRONMENT"
    BUDGET_GPU_EXCEEDED = "BUDGET_GPU_EXCEEDED"
    BUDGET_RAM_EXCEEDED = "BUDGET_RAM_EXCEEDED"
    BUDGET_TIME_EXCEEDED = "BUDGET_TIME_EXCEEDED"
    BUDGET_STORAGE_EXCEEDED = "BUDGET_STORAGE_EXCEEDED"
    CONTRACT_VIOLATION = "CONTRACT_VIOLATION"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    CLEANUP_FAILED = "CLEANUP_FAILED"


@dataclass(slots=True)
class StageMachine:
    highest_stage: Stage | None = None
    blocking_state: BlockingState | None = None
    history: list[str] = field(default_factory=list)

    @property
    def terminal(self) -> bool:
        return self.blocking_state is not None

    def expected_next(self) -> Stage | None:
        if self.terminal:
            return None
        if self.highest_stage is None:
            return STAGE_ORDER[0]
        index = STAGE_ORDER.index(self.highest_stage) + 1
        return STAGE_ORDER[index] if index < len(STAGE_ORDER) else None

    def pass_stage(self, stage: Stage) -> None:
        expected = self.expected_next()
        if expected is None or stage is not expected:
            raise ValueError(f"non-monotone stage transition: expected {expected}, got {stage}")
        self.highest_stage = stage
        self.history.append(stage.value)

    def block(self, state: BlockingState, reason: str) -> None:
        if self.terminal:
            raise ValueError(f"state machine already blocked at {self.blocking_state}")
        self.blocking_state = state
        self.history.append(f"{state.value}:{reason}")

