from __future__ import annotations

import json
from pathlib import Path

from artifactfit_mm.contracts.loader import load_contract


def test_all_declared_safety_fixture_contracts_validate() -> None:
    root = Path(__file__).parents[1] / "fixtures" / "contracts"
    directories = sorted(path for path in root.iterdir() if path.is_dir())
    assert len(directories) == 10
    for directory in directories:
        loaded = load_contract(directory / "contract.json")
        expected = json.loads((directory / "expected.json").read_text(encoding="utf-8"))
        assert loaded.contract.artifact.id == directory.name
        assert expected["fixture"] == directory.name
