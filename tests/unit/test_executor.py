from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from artifactfit_mm.runners.executor import run_contract


@pytest.mark.parametrize("run_label", ["../escape", "nested/escape", "nested\\escape", ""])
def test_run_label_cannot_escape_receipt_root(
    base_contract: dict[str, Any],
    write_contract: Any,
    workspace: Path,
    tmp_path: Path,
    run_label: str,
) -> None:
    with pytest.raises(ValueError, match="run label"):
        run_contract(
            write_contract(base_contract),
            workspace=workspace,
            receipt_root=tmp_path / "receipts",
            run_label=run_label,
        )


def test_run_label_accepts_portable_identifier(
    base_contract: dict[str, Any],
    write_contract: Any,
    workspace: Path,
    tmp_path: Path,
) -> None:
    result = run_contract(
        write_contract(base_contract),
        workspace=workspace,
        receipt_root=tmp_path / "receipts",
        run_label="audit-run_01.alpha",
    )
    assert Path(result.run_directory).name.endswith("_audit-run_01.alpha")
