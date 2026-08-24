from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

import pytest

from artifactfit_mm.runners.executor import run_contract
from artifactfit_mm.runners.replay import replay_run


def _stage(program: str, timeout: float = 5) -> dict[str, Any]:
    return {
        "enabled": True,
        "command": [sys.executable, f"programs/{program}"],
        "expected_outputs": [],
        "timeout": timeout,
        "success_predicates": [{"kind": "exit_code_zero"}],
    }


@pytest.mark.integration
@pytest.mark.parametrize("case", ["success", "timeout", "violation"])
def test_replay_stage_agreement(
    case: str,
    base_contract: dict[str, Any],
    write_contract: Any,
    workspace: Path,
    tmp_path: Path,
) -> None:
    contract = copy.deepcopy(base_contract)
    if case == "success":
        contract["stages"]["P2"] = _stage("success.py")
    elif case == "timeout":
        contract["target"]["max_wall_time"] = 0.4
        contract["stages"]["P2"] = _stage("timeout.py", timeout=0.4)
    else:
        contract["transforms"]["requested"] = [
            {"path": "input.resolution", "original": 224, "proposed": 128}
        ]
        contract["stages"]["P2"] = _stage("success.py")
    original = run_contract(
        write_contract(contract, f"{case}.yaml"),
        workspace=workspace,
        receipt_root=tmp_path / "receipts",
    )
    replay = replay_run(original.run_summary, receipt_root=tmp_path / "receipts")
    assert replay.verdict == "REPLAY_AGREEMENT"
    assert replay.stage_agreement is True
