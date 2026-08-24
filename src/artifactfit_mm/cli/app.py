from __future__ import annotations

import json
import platform
import shutil
import sys
from pathlib import Path
from typing import Annotated, Any

import psutil
import typer
import yaml
from rich.console import Console
from rich.table import Table

from artifactfit_mm import __version__
from artifactfit_mm.adapters.repository import inspect_repository
from artifactfit_mm.contracts.loader import ContractValidationError, load_contract
from artifactfit_mm.policies.engine import evaluate_transforms
from artifactfit_mm.receipts.writer import read_json
from artifactfit_mm.runners.executor import run_contract
from artifactfit_mm.runners.replay import replay_run


app = typer.Typer(
    name="artifactfit",
    help="Contract-guided, resource-bounded engineering preflight for research artifacts.",
    no_args_is_help=True,
)
console = Console()


def _emit(value: dict[str, Any], json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False))
        return
    table = Table(show_header=False)
    table.add_column("Field", style="bold")
    table.add_column("Value")
    for key, item in value.items():
        rendered = json.dumps(item, ensure_ascii=False) if isinstance(item, (dict, list)) else str(item)
        table.add_row(key, rendered)
    console.print(table)


def _abort(message: str, json_output: bool, code: int = 2) -> None:
    if json_output:
        typer.echo(json.dumps({"status": "ERROR", "message": message}, ensure_ascii=False))
    else:
        console.print(f"[red]ERROR:[/red] {message}")
    raise typer.Exit(code)


def _template() -> dict[str, Any]:
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
            "id": "replace-me",
            "repository": "https://example.invalid/owner/repository",
            "commit": "0000000000000000000000000000000000000000",
            "license_policy": {
                "allowed_spdx": ["MIT"],
                "require_license_file": True,
                "allow_unknown": False,
            },
            "official_entrypoints": ["python -m package --help"],
            "declared_framework": "replace-me",
        },
        "target": {
            "platform": "windows",
            "gpu_count": 1,
            "max_vram_gb": 12,
            "max_system_ram_gb": 28,
            "max_wall_time": 900,
            "max_workspace_growth_gb": 2,
            "max_download_gb": 0,
        },
        "declared_invariants": {
            "task": "replace-me",
            "modalities": ["image"],
            "model_family": "replace-me",
            "backbone_constraints": ["unchanged"],
            "input_constraints": ["unchanged resolution"],
            "dataset_semantics": "unchanged",
            "evaluation_protocol": "unchanged",
            "objective": "engineering feasibility only",
        },
        "transforms": {
            "allowed": ["training.batch_size", "runtime.num_workers"],
            "review_required": ["model.gradient_checkpointing"],
            "forbidden": ["model.backbone", "input.resolution"],
            "requested": [],
        },
        "stages": {"P0": internal, "P1": internal},
        "evidence": {
            "files_to_hash": ["README.md", "LICENSE"],
            "environment_capture": True,
            "resource_trace": True,
            "stdout": True,
            "stderr": True,
        },
        "runtime": {
            "network_allowed": False,
            "sample_interval_ms": 100,
            "stdout_limit_bytes": 1048576,
            "stderr_limit_bytes": 1048576,
            "require_clean_exit": True,
        },
    }


@app.command("init")
def init_contract(
    path: Annotated[Path, typer.Argument(help="New YAML contract path")],
    force: Annotated[bool, typer.Option("--force", help="Replace an existing template")] = False,
) -> None:
    """Create a fail-closed contract template without contacting a network."""
    target = path.resolve()
    if target.exists() and not force:
        _abort(f"refusing to overwrite existing file: {target}", False)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(_template(), sort_keys=False), encoding="utf-8")
    console.print(f"Created {target}")


@app.command()
def validate(
    contract: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Validate syntax, independent JSON Schema, and semantic constraints."""
    try:
        loaded = load_contract(contract)
    except ContractValidationError as exc:
        _abort(str(exc), json_output)
    _emit(
        {
            "status": "VALID",
            "contract": str(loaded.source_path),
            "contract_hash": loaded.contract_hash,
            "migrations": list(loaded.migrations),
        },
        json_output,
    )


@app.command()
def inspect(
    contract: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    workspace: Annotated[Path | None, typer.Option("--workspace", file_okay=False)] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect a frozen contract and optionally its local repository checkout."""
    try:
        loaded = load_contract(contract)
    except ContractValidationError as exc:
        _abort(str(exc), json_output)
    value: dict[str, Any] = {
        "artifact_id": loaded.contract.artifact.id,
        "repository": loaded.contract.artifact.repository,
        "declared_commit": loaded.contract.artifact.commit,
        "contract_hash": loaded.contract_hash,
        "target": loaded.contract.target.model_dump(mode="json"),
        "enabled_stages": [key for key, stage in loaded.contract.stages.items() if stage.enabled],
        "network_allowed": loaded.contract.runtime.network_allowed,
    }
    if workspace is not None:
        value["repository_inspection"] = inspect_repository(
            workspace, loaded.contract.artifact
        ).as_dict()
    _emit(value, json_output)


@app.command()
def plan(
    contract: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Render the exact bounded plan; this command never executes external code."""
    try:
        loaded = load_contract(contract)
    except ContractValidationError as exc:
        _abort(str(exc), json_output)
    policy = evaluate_transforms(loaded.contract)
    stages = [
        {
            "stage": key,
            "command": stage.command,
            "timeout": stage.timeout,
            "working_directory": stage.working_directory,
        }
        for key, stage in sorted(loaded.contract.stages.items())
        if stage.enabled
    ]
    _emit(
        {
            "execution_performed": False,
            "contract_hash": loaded.contract_hash,
            "policy": policy.as_dict(),
            "stages": stages,
            "network": "EXPLICIT_ALLOW" if loaded.contract.runtime.network_allowed else "DEFAULT_DENY",
        },
        json_output,
    )


@app.command()
def run(
    contract: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    workspace: Annotated[Path, typer.Option("--workspace", file_okay=False)],
    receipt_root: Annotated[Path, typer.Option("--receipt-root", file_okay=False)],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Execute only contract-authorized stages under resource monitoring."""
    try:
        result = run_contract(contract, workspace=workspace, receipt_root=receipt_root)
    except (ContractValidationError, OSError, ValueError) as exc:
        _abort(str(exc), json_output)
    _emit(result.as_dict(), json_output)


@app.command()
def replay(
    receipt: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    receipt_root: Annotated[Path | None, typer.Option("--receipt-root", file_okay=False)] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Fail closed unless contract hash, commit, and recorded commands remain identical."""
    try:
        result = replay_run(receipt, receipt_root=receipt_root)
    except (OSError, ValueError, KeyError) as exc:
        _abort(str(exc), json_output)
    _emit(result.as_dict(), json_output)


@app.command()
def report(
    receipt: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Render a receipt or run summary without executing external code."""
    try:
        value = read_json(receipt)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        _abort(str(exc), json_output)
    _emit(value, json_output)


@app.command()
def doctor(
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Report host capabilities and enforcement limitations."""
    nvml_status = "UNVERIFIED"
    try:
        import pynvml

        pynvml.nvmlInit()
        nvml_status = f"AVAILABLE:{pynvml.nvmlDeviceGetCount()}_DEVICE(S)"
        pynvml.nvmlShutdown()
    except Exception as exc:  # noqa: BLE001
        nvml_status = f"UNAVAILABLE:{type(exc).__name__}"
    _emit(
        {
            "tool_version": __version__,
            "python": sys.version,
            "platform": platform.platform(),
            "ram_total_gb": round(psutil.virtual_memory().total / (1024**3), 3),
            "git": shutil.which("git") or "NOT_FOUND",
            "nvml": nvml_status,
            "network_enforcement": "DECLARATIVE_AND_LIBRARY_OFFLINE_FLAGS_ONLY",
            "download_measurement": "DOWNLOAD_BYTES_UNMEASURED_FOR_EXTERNAL_PROGRAMS",
            "scientific_result": False,
        },
        json_output,
    )


if __name__ == "__main__":
    app()
