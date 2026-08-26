from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import PurePath, PurePosixPath, PureWindowsPath
from typing import Any

from artifactfit_mm.contracts.models import (
    ArtifactContract,
    TransformClass,
    TransformRequest,
)

SAFE_KEYS = {
    "batch_size",
    "evaluation_batch_size",
    "eval_batch_size",
    "num_workers",
    "gradient_accumulation",
    "gradient_accumulation_steps",
    "amp",
    "fp16",
    "bf16",
    "log_frequency",
    "logging_steps",
    "output_directory",
    "output_dir",
    "seed",
    "cache_location",
    "cache_dir",
}
REVIEW_KEYS = {
    "gradient_checkpointing",
    "compile",
    "compile_mode",
    "tokenizer_truncation",
    "test_time_chunking",
    "sequence_length",
    "max_sequence_length",
    "frame_sampling_frequency",
    "frame_stride",
}
FORBIDDEN_KEYS = {
    "model_family",
    "backbone",
    "backbone_family",
    "task",
    "modalities",
    "modality",
    "dataset_semantics",
    "dataset",
    "evaluation_protocol",
    "objective",
    "loss",
    "input_resolution",
    "resolution",
    "crop_policy",
    "field_of_view",
    "class_vocabulary",
    "classes",
    "label_mapping",
}


@dataclass(frozen=True, slots=True)
class TransformDecision:
    path: str
    original: Any
    proposed: Any
    classification: TransformClass
    rule: str
    rationale: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "original": self.original,
            "proposed": self.proposed,
            "classification": self.classification.value,
            "rule": self.rule,
            "rationale": self.rationale,
        }


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    decisions: tuple[TransformDecision, ...]

    @property
    def final_class(self) -> TransformClass:
        classes = {item.classification for item in self.decisions}
        if TransformClass.CONTRACT_VIOLATION in classes:
            return TransformClass.CONTRACT_VIOLATION
        if TransformClass.REVIEW_REQUIRED in classes:
            return TransformClass.REVIEW_REQUIRED
        return TransformClass.SAFE

    def as_dict(self) -> dict[str, Any]:
        return {
            "final_class": self.final_class.value,
            "decisions": [item.as_dict() for item in self.decisions],
        }


def _matches(path: str, patterns: list[str]) -> bool:
    lowered = path.casefold()
    return any(fnmatch.fnmatchcase(lowered, pattern.casefold()) for pattern in patterns)


def _leaf(path: str) -> str:
    normalized = path.replace("[", ".").replace("]", "")
    return normalized.rsplit(".", maxsplit=1)[-1].casefold()


def _unsafe_path_value(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    candidates = (PurePath(value), PureWindowsPath(value), PurePosixPath(value))
    return any(candidate.is_absolute() or ".." in candidate.parts for candidate in candidates)


def _classify_builtin(request: TransformRequest) -> tuple[TransformClass, str, str]:
    leaf = _leaf(request.path)
    lowered = request.path.casefold()
    if leaf in FORBIDDEN_KEYS or any(f".{key}." in f".{lowered}." for key in FORBIDDEN_KEYS):
        return (
            TransformClass.CONTRACT_VIOLATION,
            f"builtin.forbidden.{leaf}",
            "The transformation changes a declared scientific invariant.",
        )
    if leaf in REVIEW_KEYS:
        return (
            TransformClass.REVIEW_REQUIRED,
            f"builtin.review.{leaf}",
            "The transformation may preserve semantics but requires artifact-specific review.",
        )
    if leaf in SAFE_KEYS:
        if leaf in {"batch_size", "evaluation_batch_size", "eval_batch_size"} and not (
            isinstance(request.original, (int, float))
            and not isinstance(request.original, bool)
            and isinstance(request.proposed, (int, float))
            and not isinstance(request.proposed, bool)
            and 0 < request.proposed <= request.original
        ):
            return (
                TransformClass.REVIEW_REQUIRED,
                f"builtin.review.{leaf}.not_reduction",
                "Only a positive batch-size reduction is safe by default.",
            )
        if leaf in {
            "output_directory",
            "output_dir",
            "cache_location",
            "cache_dir",
        } and _unsafe_path_value(request.proposed):
            return (
                TransformClass.CONTRACT_VIOLATION,
                f"builtin.forbidden.{leaf}.workspace_escape",
                "Output and cache paths must remain inside the artifact workspace.",
            )
        return (
            TransformClass.SAFE,
            f"builtin.safe.{leaf}",
            "The transformation is an engineering control that does not change a frozen invariant.",
        )
    return (
        TransformClass.REVIEW_REQUIRED,
        "builtin.fail_closed.unknown",
        "Unknown transformations are never accepted automatically.",
    )


def classify_transform(contract: ArtifactContract, request: TransformRequest) -> TransformDecision:
    builtin_class, builtin_rule, rationale = _classify_builtin(request)
    policy = contract.transforms
    classification: TransformClass
    rule: str
    if builtin_class is TransformClass.CONTRACT_VIOLATION:
        classification = builtin_class
        rule = builtin_rule
    elif _matches(request.path, policy.forbidden):
        classification = TransformClass.CONTRACT_VIOLATION
        rule = "contract.forbidden"
        rationale = "The frozen contract explicitly forbids this path."
    elif _matches(request.path, policy.review_required):
        classification = TransformClass.REVIEW_REQUIRED
        rule = "contract.review_required"
        rationale = "The frozen contract explicitly requires review for this path."
    elif _matches(request.path, policy.allowed):
        if builtin_class is TransformClass.REVIEW_REQUIRED:
            classification = TransformClass.REVIEW_REQUIRED
            rule = builtin_rule
        else:
            classification = TransformClass.SAFE
            rule = "contract.allowed"
            rationale = "Both the built-in taxonomy and frozen contract allow this path."
    else:
        classification = builtin_class
        rule = builtin_rule
    return TransformDecision(
        path=request.path,
        original=request.original,
        proposed=request.proposed,
        classification=classification,
        rule=rule,
        rationale=rationale,
    )


def evaluate_transforms(contract: ArtifactContract) -> PolicyDecision:
    return PolicyDecision(
        decisions=tuple(
            classify_transform(contract, item) for item in contract.transforms.requested
        )
    )
