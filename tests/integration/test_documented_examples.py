from pathlib import Path

from artifactfit_mm.contracts.loader import load_contract


def test_all_documented_contract_examples_validate() -> None:
    example_root = Path(__file__).resolve().parents[2] / "examples"
    examples = sorted(example_root.glob("*.yaml"))
    assert examples, "at least one documented contract example must exist"
    for example in examples:
        loaded = load_contract(example)
        assert loaded.contract.schema_version == "1.0.0"
        assert len(loaded.contract_hash) == 64
