from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

import psutil
import pytest

from artifactfit_mm.runners.executor import run_contract
from artifactfit_mm.stages.machine import BlockingState, Stage


def _stage(program: str, timeout: float = 5) -> dict[str, Any]:
    return {
        "enabled": True,
        "command": [sys.executable, f"programs/{program}"],
        "expected_outputs": [],
        "timeout": timeout,
        "success_predicates": [{"kind": "exit_code_zero"}],
        "working_directory": ".",
        "environment": {},
    }


def _receipt(result: Any, key: str) -> dict[str, Any]:
    path = next(Path(item) for item in result.receipt_paths if f"{key}_" in item)
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.integration
def test_successful_stage_emits_complete_receipt(
    base_contract: dict[str, Any], write_contract: Any, workspace: Path, tmp_path: Path
) -> None:
    base_contract["stages"]["P2"] = _stage("success.py")
    base_contract["stages"]["P2"]["expected_outputs"] = ["output.txt"]
    base_contract["stages"]["P2"]["success_predicates"].append(
        {"kind": "path_exists", "value": "output.txt"}
    )
    result = run_contract(
        write_contract(base_contract), workspace=workspace, receipt_root=tmp_path / "receipts"
    )
    assert result.observed_highest_stage == Stage.P2_ENVIRONMENT_READY.value
    receipt = _receipt(result, "P2")
    assert receipt["ENGINEERING_FEASIBILITY_ONLY"] is True
    assert receipt["SCIENTIFIC_RESULT"] is False
    assert receipt["PAPER_CLAIM_REPRODUCED"] == "not_assessed"
    for filename in (
        "receipt.md",
        "resource_trace.csv",
        "stdout.log",
        "stderr.log",
        "environment.json",
        "command.json",
        "transform_diff.json",
        "hash_manifest.txt",
    ):
        assert (
            Path(next(item for item in result.receipt_paths if "P2_" in item)).parent / filename
        ).exists()


@pytest.mark.integration
def test_timeout_terminates_tree(
    base_contract: dict[str, Any], write_contract: Any, workspace: Path, tmp_path: Path
) -> None:
    base_contract["target"]["max_wall_time"] = 0.5
    base_contract["stages"]["P2"] = _stage("timeout.py", timeout=0.5)
    result = run_contract(
        write_contract(base_contract), workspace=workspace, receipt_root=tmp_path / "receipts"
    )
    assert result.blocking_state == BlockingState.BUDGET_TIME_EXCEEDED.value
    assert _receipt(result, "P2")["cleanup_status"] == "CLEAN"


@pytest.mark.integration
def test_ram_budget_terminates_tree(
    base_contract: dict[str, Any], write_contract: Any, workspace: Path, tmp_path: Path
) -> None:
    base_contract["target"]["max_system_ram_gb"] = 0.06
    base_contract["stages"]["P2"] = _stage("ram_exhaustion.py")
    result = run_contract(
        write_contract(base_contract), workspace=workspace, receipt_root=tmp_path / "receipts"
    )
    assert result.blocking_state == BlockingState.BUDGET_RAM_EXCEEDED.value
    assert _receipt(result, "P2")["cleanup_status"] == "CLEAN"


@pytest.mark.integration
def test_disk_growth_budget_terminates_tree(
    base_contract: dict[str, Any], write_contract: Any, workspace: Path, tmp_path: Path
) -> None:
    base_contract["target"]["max_workspace_growth_gb"] = 0.002
    base_contract["stages"]["P2"] = _stage("disk_growth.py")
    result = run_contract(
        write_contract(base_contract), workspace=workspace, receipt_root=tmp_path / "receipts"
    )
    assert result.blocking_state == BlockingState.BUDGET_STORAGE_EXCEEDED.value
    assert _receipt(result, "P2")["cleanup_status"] == "CLEAN"


@pytest.mark.integration
def test_stdout_flood_is_truncated_without_stage_failure(
    base_contract: dict[str, Any], write_contract: Any, workspace: Path, tmp_path: Path
) -> None:
    base_contract["runtime"]["stdout_limit_bytes"] = 65536
    base_contract["stages"]["P2"] = _stage("stdout_flood.py")
    result = run_contract(
        write_contract(base_contract), workspace=workspace, receipt_root=tmp_path / "receipts"
    )
    receipt = _receipt(result, "P2")
    assert result.observed_highest_stage == Stage.P2_ENVIRONMENT_READY.value
    assert receipt["stdout_truncated"] is True
    assert receipt["stdout_observed_bytes"] > receipt["stdout_written_bytes"]


@pytest.mark.integration
def test_child_process_is_cleaned(
    base_contract: dict[str, Any], write_contract: Any, workspace: Path, tmp_path: Path
) -> None:
    base_contract["stages"]["P2"] = _stage("child_process_leak.py")
    result = run_contract(
        write_contract(base_contract), workspace=workspace, receipt_root=tmp_path / "receipts"
    )
    child_pid = int((workspace / "child.pid").read_text(encoding="utf-8"))
    assert result.observed_highest_stage == Stage.P2_ENVIRONMENT_READY.value
    assert not psutil.pid_exists(child_pid)
    assert _receipt(result, "P2")["cleanup_status"] == "CLEAN"


@pytest.mark.integration
@pytest.mark.parametrize(
    ("path", "original", "proposed"),
    [
        ("input.resolution", 224, 128),
        ("model.backbone", "ViT-B/32", "RN50"),
        ("input.modalities", ["image", "text"], ["image"]),
    ],
)
def test_claim_changing_transform_never_executes(
    path: str,
    original: object,
    proposed: object,
    base_contract: dict[str, Any],
    write_contract: Any,
    workspace: Path,
    tmp_path: Path,
) -> None:
    contract = copy.deepcopy(base_contract)
    contract["transforms"]["requested"] = [
        {"path": path, "original": original, "proposed": proposed}
    ]
    contract["stages"]["P2"] = _stage("success.py")
    result = run_contract(
        write_contract(contract, f"{path.replace('.', '_')}.yaml"),
        workspace=workspace,
        receipt_root=tmp_path / "receipts",
    )
    assert result.blocking_state == BlockingState.CONTRACT_VIOLATION.value
    assert not (workspace / "output.txt").exists()


@pytest.mark.integration
def test_safe_batch_transform_proceeds(
    base_contract: dict[str, Any], write_contract: Any, workspace: Path, tmp_path: Path
) -> None:
    base_contract["transforms"]["requested"] = [
        {"path": "training.batch_size", "original": 32, "proposed": 4}
    ]
    base_contract["stages"]["P2"] = _stage("success.py")
    result = run_contract(
        write_contract(base_contract), workspace=workspace, receipt_root=tmp_path / "receipts"
    )
    assert result.observed_highest_stage == Stage.P2_ENVIRONMENT_READY.value
