from __future__ import annotations

import copy
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    source = Path(__file__).parent / "fixtures" / "workspace"
    target = tmp_path / "workspace"
    shutil.copytree(source, target)
    return target


@pytest.fixture()
def base_contract() -> dict[str, Any]:
    internal = {
        "enabled": True,
        "command": ["artifactfit:internal"],
        "expected_outputs": [],
        "timeout": 5,
        "success_predicates": [{"kind": "exit_code_zero"}],
    }
    return {
        "schema_version": "1.0.0",
        "artifact": {
            "id": "fixture",
            "repository": "local://fixture",
            "commit": "fixture0001",
            "license_policy": {
                "allowed_spdx": ["MIT"],
                "require_license_file": True,
                "allow_unknown": False,
            },
            "official_entrypoints": ["programs/success.py"],
            "declared_framework": "python",
        },
        "target": {
            "platform": "windows",
            "gpu_count": 0,
            "max_vram_gb": 0,
            "max_system_ram_gb": 1,
            "max_wall_time": 10,
            "max_workspace_growth_gb": 0.05,
            "max_download_gb": 0,
        },
        "declared_invariants": {
            "task": "synthetic engineering fixture",
            "modalities": ["none"],
            "model_family": "none",
            "backbone_constraints": ["unchanged"],
            "input_constraints": ["unchanged"],
            "dataset_semantics": "none",
            "evaluation_protocol": "process exit and evidence predicates",
            "objective": "engineering feasibility only",
        },
        "transforms": {
            "allowed": ["training.batch_size", "runtime.num_workers", "runtime.output_dir"],
            "review_required": ["model.gradient_checkpointing"],
            "forbidden": [
                "model.backbone",
                "input.resolution",
                "input.modalities",
            ],
            "requested": [],
        },
        "stages": {"P0": copy.deepcopy(internal), "P1": copy.deepcopy(internal)},
        "evidence": {
            "files_to_hash": ["README.md", "LICENSE", "output.txt"],
            "environment_capture": True,
            "resource_trace": True,
            "stdout": True,
            "stderr": True,
        },
        "runtime": {
            "network_allowed": False,
            "sample_interval_ms": 50,
            "stdout_limit_bytes": 65536,
            "stderr_limit_bytes": 65536,
            "require_clean_exit": True,
        },
    }


@pytest.fixture()
def write_contract(tmp_path: Path) -> Callable[[dict[str, Any], str], Path]:
    def _write(value: dict[str, Any], name: str = "contract.yaml") -> Path:
        path = tmp_path / name
        path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
        return path

    return _write


def external_stage(program: str, *, timeout: float = 5) -> dict[str, Any]:
    return {
        "enabled": True,
        "command": [sys.executable, f"programs/{program}"],
        "expected_outputs": [],
        "timeout": timeout,
        "success_predicates": [{"kind": "exit_code_zero"}],
        "working_directory": ".",
        "environment": {},
    }
