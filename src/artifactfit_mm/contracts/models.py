from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


PositiveFloat = Annotated[float, Field(gt=0)]
NonNegativeFloat = Annotated[float, Field(ge=0)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LicensePolicy(StrictModel):
    allowed_spdx: list[str] = Field(min_length=1)
    require_license_file: bool = True
    allow_unknown: bool = False


class ArtifactSpec(StrictModel):
    id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_.-]+$")
    repository: str = Field(min_length=1)
    commit: str = Field(min_length=7)
    license_policy: LicensePolicy
    official_entrypoints: list[str] = Field(min_length=1)
    declared_framework: str = Field(min_length=1)


class TargetSpec(StrictModel):
    platform: Literal["windows", "linux", "wsl2"]
    gpu_count: int = Field(ge=0)
    max_vram_gb: NonNegativeFloat
    max_system_ram_gb: PositiveFloat
    max_wall_time: PositiveFloat
    max_workspace_growth_gb: NonNegativeFloat
    max_download_gb: NonNegativeFloat


class DeclaredInvariants(StrictModel):
    task: str = Field(min_length=1)
    modalities: list[str] = Field(min_length=1)
    model_family: str = Field(min_length=1)
    backbone_constraints: list[str] = Field(min_length=1)
    input_constraints: list[str] = Field(min_length=1)
    dataset_semantics: str = Field(min_length=1)
    evaluation_protocol: str = Field(min_length=1)
    objective: str = Field(min_length=1)


class TransformClass(StrEnum):
    SAFE = "SAFE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    CONTRACT_VIOLATION = "CONTRACT_VIOLATION"


class TransformRequest(StrictModel):
    path: str = Field(min_length=1)
    original: object
    proposed: object


class TransformPolicySpec(StrictModel):
    allowed: list[str]
    review_required: list[str]
    forbidden: list[str]
    requested: list[TransformRequest] = Field(default_factory=list)

    @model_validator(mode="after")
    def disjoint_rules(self) -> TransformPolicySpec:
        groups = [set(self.allowed), set(self.review_required), set(self.forbidden)]
        if groups[0] & groups[1] or groups[0] & groups[2] or groups[1] & groups[2]:
            raise ValueError("transform rule names must be disjoint")
        return self


class PredicateKind(StrEnum):
    EXIT_CODE_ZERO = "exit_code_zero"
    PATH_EXISTS = "path_exists"
    STDOUT_CONTAINS = "stdout_contains"
    STDERR_NOT_CONTAINS = "stderr_not_contains"


class SuccessPredicate(StrictModel):
    kind: PredicateKind
    value: str | None = None

    @model_validator(mode="after")
    def value_required(self) -> SuccessPredicate:
        if self.kind is not PredicateKind.EXIT_CODE_ZERO and not self.value:
            raise ValueError(f"predicate {self.kind} requires value")
        return self


class StageSpec(StrictModel):
    enabled: bool
    command: list[str] = Field(min_length=1)
    expected_outputs: list[str]
    timeout: PositiveFloat
    success_predicates: list[SuccessPredicate] = Field(min_length=1)
    working_directory: str = "."
    environment: dict[str, str] = Field(default_factory=dict)

    @field_validator("working_directory")
    @classmethod
    def relative_workdir(cls, value: str) -> str:
        if Path(value).is_absolute() or ".." in Path(value).parts:
            raise ValueError("stage working_directory must stay inside artifact workspace")
        return value


class EvidenceSpec(StrictModel):
    files_to_hash: list[str]
    environment_capture: bool = True
    resource_trace: bool = True
    stdout: bool = True
    stderr: bool = True


class RuntimePolicy(StrictModel):
    network_allowed: bool = False
    sample_interval_ms: int = Field(default=100, ge=50, le=5000)
    stdout_limit_bytes: int = Field(default=1_048_576, ge=1024)
    stderr_limit_bytes: int = Field(default=1_048_576, ge=1024)
    require_clean_exit: bool = True


class ArtifactContract(StrictModel):
    schema_version: Literal["1.0.0"]
    artifact: ArtifactSpec
    target: TargetSpec
    declared_invariants: DeclaredInvariants
    transforms: TransformPolicySpec
    stages: dict[str, StageSpec]
    evidence: EvidenceSpec
    runtime: RuntimePolicy = Field(default_factory=RuntimePolicy)

    @model_validator(mode="after")
    def known_stage_keys(self) -> ArtifactContract:
        known = {f"P{i}" for i in range(9)}
        unknown = set(self.stages) - known
        if unknown:
            raise ValueError(f"unknown stage keys: {sorted(unknown)}")
        if "P0" not in self.stages or "P1" not in self.stages:
            raise ValueError("P0 and P1 must be declared")
        return self

