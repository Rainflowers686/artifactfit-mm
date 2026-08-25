from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from artifactfit_mm.contracts.loader import ContractValidationError, load_contract


def test_valid_contract_has_stable_hash(base_contract: dict[str, Any], write_contract: Any) -> None:
    path = write_contract(base_contract)
    first = load_contract(path)
    second = load_contract(path)
    assert first.contract_hash == second.contract_hash
    assert len(first.contract_hash) == 64


def test_unknown_field_fails_closed(base_contract: dict[str, Any], write_contract: Any) -> None:
    base_contract["undeclared"] = "must fail"
    path = write_contract(base_contract)
    with pytest.raises(ContractValidationError, match="Additional properties"):
        load_contract(path)


def test_old_schema_migrates_with_explicit_note(
    base_contract: dict[str, Any], tmp_path: Path
) -> None:
    base_contract["schema_version"] = "0.1.0"
    base_contract.pop("runtime")
    path = tmp_path / "old.yaml"
    path.write_text(yaml.safe_dump(base_contract, sort_keys=False), encoding="utf-8")
    loaded = load_contract(path)
    assert loaded.contract.schema_version == "1.0.0"
    assert loaded.contract.runtime.network_allowed is False
    assert loaded.migrations


@pytest.mark.parametrize(
    "working_directory", ["../outside", "..\\outside", "C:\\outside", "/outside"]
)
def test_stage_workdir_cannot_escape_on_windows_or_wsl(
    base_contract: dict[str, Any], write_contract: Any, working_directory: str
) -> None:
    base_contract["stages"]["P2"] = {
        "enabled": True,
        "command": ["python", "-V"],
        "expected_outputs": [],
        "timeout": 5,
        "success_predicates": [{"kind": "exit_code_zero"}],
        "working_directory": working_directory,
    }
    with pytest.raises(ContractValidationError, match="stay inside"):
        load_contract(write_contract(base_contract))


def test_stage_workdir_accepts_contained_native_path(
    base_contract: dict[str, Any], write_contract: Any
) -> None:
    base_contract["stages"]["P2"] = {
        "enabled": True,
        "command": ["python", "-V"],
        "expected_outputs": [],
        "timeout": 5,
        "success_predicates": [{"kind": "exit_code_zero"}],
        "working_directory": "contained/subdirectory",
    }
    assert load_contract(write_contract(base_contract)).contract.stages["P2"].working_directory == (
        "contained/subdirectory"
    )
