from __future__ import annotations

from typing import Any

import pytest

from artifactfit_mm.contracts.loader import load_contract
from artifactfit_mm.contracts.models import TransformClass
from artifactfit_mm.policies.engine import evaluate_transforms


def _decision(
    base_contract: dict[str, Any],
    write_contract: Any,
    path: str,
    original: object,
    proposed: object,
) -> TransformClass:
    base_contract["transforms"]["requested"] = [
        {"path": path, "original": original, "proposed": proposed}
    ]
    contract = load_contract(write_contract(base_contract)).contract
    return evaluate_transforms(contract).final_class


def test_batch_reduction_is_safe(base_contract: dict[str, Any], write_contract: Any) -> None:
    assert (
        _decision(base_contract, write_contract, "training.batch_size", 32, 4)
        is TransformClass.SAFE
    )


def test_batch_increase_requires_review(base_contract: dict[str, Any], write_contract: Any) -> None:
    assert (
        _decision(base_contract, write_contract, "training.batch_size", 4, 32)
        is TransformClass.REVIEW_REQUIRED
    )


def test_resolution_change_is_violation(base_contract: dict[str, Any], write_contract: Any) -> None:
    assert (
        _decision(base_contract, write_contract, "input.resolution", 224, 128)
        is TransformClass.CONTRACT_VIOLATION
    )


def test_unknown_change_fails_to_review(base_contract: dict[str, Any], write_contract: Any) -> None:
    assert (
        _decision(base_contract, write_contract, "optimizer.experimental_toggle", False, True)
        is TransformClass.REVIEW_REQUIRED
    )


@pytest.mark.parametrize(
    "proposed",
    [
        "../outside",
        "/outside",
        "C:\\outside",
        "C:/outside",
        "\\\\server\\share\\outside",
        "folder/../../outside",
    ],
)
def test_workspace_escape_is_violation(
    base_contract: dict[str, Any], write_contract: Any, proposed: str
) -> None:
    assert (
        _decision(base_contract, write_contract, "runtime.output_dir", "outputs", proposed)
        is TransformClass.CONTRACT_VIOLATION
    )
