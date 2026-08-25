from __future__ import annotations

import json
import os
import socket
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from artifactfit_mm import __version__
from artifactfit_mm.adapters.repository import RepositoryInspection, inspect_repository
from artifactfit_mm.contracts.loader import LoadedContract, load_contract
from artifactfit_mm.contracts.models import PredicateKind, StageSpec, TransformClass
from artifactfit_mm.policies.engine import PolicyDecision, evaluate_transforms
from artifactfit_mm.receipts.writer import (
    FIXED_NON_CLAIMS,
    capture_environment,
    sha256_file,
    write_stage_receipt,
)
from artifactfit_mm.resources.process import GIB, ProcessLimits, ProcessResult, run_bounded_process
from artifactfit_mm.stages.machine import BlockingState, Stage, StageMachine

STAGE_BY_KEY: dict[str, Stage] = {
    "P0": Stage.P0_DISCOVERED,
    "P1": Stage.P1_POLICY_ACCEPTED,
    "P2": Stage.P2_ENVIRONMENT_READY,
    "P3": Stage.P3_IMPORT_PASS,
    "P4": Stage.P4_ENTRYPOINT_PASS,
    "P5": Stage.P5_DEMO_PASS,
    "P6": Stage.P6_FORWARD_PASS,
    "P7": Stage.P7_BACKWARD_SMOKE_PASS,
    "P8": Stage.P8_SHORT_OPTIMIZATION_PASS,
}


@dataclass(frozen=True, slots=True)
class RunResult:
    run_directory: str
    run_summary: str
    observed_highest_stage: str | None
    blocking_state: str | None
    final_verdict: str
    receipt_paths: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _timestamp_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _contained_path(workspace: Path, relative: str) -> Path:
    candidate = (workspace / relative).resolve()
    try:
        candidate.relative_to(workspace.resolve())
    except ValueError as exc:
        raise ValueError(f"path escapes workspace: {relative}") from exc
    return candidate


def command_file_hashes(command: list[str], working_directory: Path) -> list[dict[str, object]]:
    """Hash every existing file named directly in an argv vector.

    Contract hashing protects the command text. These records additionally
    protect scripts, configs, checkpoints, and executables referenced by that
    command, including files outside the artifact workspace.
    """
    records: list[dict[str, object]] = []
    seen: set[Path] = set()
    for argument in command:
        try:
            candidate = Path(argument)
            if not candidate.is_absolute():
                candidate = working_directory / candidate
            resolved = candidate.resolve()
            if not resolved.is_file() or resolved in seen:
                continue
        except (OSError, ValueError):
            continue
        seen.add(resolved)
        records.append(
            {
                "path": str(resolved),
                "size": resolved.stat().st_size,
                "sha256": sha256_file(resolved),
            }
        )
    return records


def _observed_outputs(workspace: Path, expected: list[str]) -> list[dict[str, object]]:
    values: list[dict[str, object]] = []
    for relative in expected:
        try:
            path = _contained_path(workspace, relative)
        except ValueError:
            values.append({"path": relative, "exists": False, "status": "OUTSIDE_WORKSPACE"})
            continue
        values.append(
            {
                "path": relative,
                "exists": path.exists(),
                "type": "file" if path.is_file() else "directory" if path.is_dir() else "missing",
                "size": path.stat().st_size if path.is_file() else None,
            }
        )
    return values


def _read_log(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _predicates_pass(
    spec: StageSpec,
    workspace: Path,
    process_result: ProcessResult,
    stdout_path: Path,
    stderr_path: Path,
) -> tuple[bool, list[dict[str, object]]]:
    stdout = _read_log(stdout_path)
    stderr = _read_log(stderr_path)
    records: list[dict[str, object]] = []
    all_pass = True
    for predicate in spec.success_predicates:
        if predicate.kind is PredicateKind.EXIT_CODE_ZERO:
            passed = process_result.exit_code == 0
        elif predicate.kind is PredicateKind.PATH_EXISTS:
            assert predicate.value is not None
            try:
                passed = _contained_path(workspace, predicate.value).exists()
            except ValueError:
                passed = False
        elif predicate.kind is PredicateKind.STDOUT_CONTAINS:
            assert predicate.value is not None
            passed = predicate.value in stdout
        elif predicate.kind is PredicateKind.STDERR_NOT_CONTAINS:
            assert predicate.value is not None
            passed = predicate.value not in stderr
        else:  # pragma: no cover - enum and schema close this path
            passed = False
        records.append({"kind": predicate.kind.value, "value": predicate.value, "passed": passed})
        all_pass = all_pass and passed
    return all_pass, records


def _base_receipt(
    loaded: LoadedContract,
    inspection: RepositoryInspection,
    workspace: Path,
    stage: Stage,
    machine: StageMachine,
    policy: PolicyDecision,
    *,
    final_verdict: str,
    cleanup_status: str,
) -> dict[str, Any]:
    environment = capture_environment()
    return {
        "host": environment["host"],
        "os": environment["platform"],
        "ram_total_bytes": environment["ram_total_bytes"],
        "gpu": "captured_by_resource_trace_or_unavailable",
        "repository": loaded.contract.artifact.repository,
        "workspace": str(workspace),
        "declared_commit": loaded.contract.artifact.commit,
        "observed_commit": inspection.observed_commit,
        "license": inspection.detected_spdx,
        "contract_path": str(loaded.source_path),
        "contract_hash": loaded.contract_hash,
        "stage": stage.value,
        "observed_highest_stage": machine.highest_stage.value if machine.highest_stage else None,
        "blocking_state": machine.blocking_state.value if machine.blocking_state else None,
        "stage_history": list(machine.history),
        "transforms": policy.as_dict(),
        "cleanup_status": cleanup_status,
        "final_verdict": final_verdict,
    }


def _write_internal_receipt(
    run_directory: Path,
    key: str,
    *,
    loaded: LoadedContract,
    inspection: RepositoryInspection,
    workspace: Path,
    machine: StageMachine,
    policy: PolicyDecision,
    verdict: str,
    observed_outputs: list[dict[str, object]],
) -> str:
    stage = STAGE_BY_KEY[key]
    receipt = _base_receipt(
        loaded,
        inspection,
        workspace,
        stage,
        machine,
        policy,
        final_verdict=verdict,
        cleanup_status="NOT_APPLICABLE_INTERNAL_STAGE",
    )
    receipt.update(
        {
            "command": [f"artifactfit:internal:{key.casefold()}"],
            "environment": {},
            "start_time": None,
            "end_time": None,
            "exit_code": None,
            "timeout": False,
            "peak_gpu_bytes": None,
            "peak_ram_bytes": None,
            "workspace_delta_bytes": 0,
            "enforcement_mode": {"execution": "NOT_APPLICABLE_INTERNAL_STAGE"},
            "expected_outputs": [],
            "observed_outputs": observed_outputs,
        }
    )
    path = write_stage_receipt(
        run_directory / f"{key}_{stage.value}",
        receipt=receipt,
        command={"argv": [f"artifactfit:internal:{key.casefold()}"], "internal": True},
        transform_diff=policy.as_dict(),
        workspace=workspace,
        files_to_hash=loaded.contract.evidence.files_to_hash,
        process_result=None,
    )
    return str(path)


def _runtime_environment(
    network_allowed: bool, stage_environment: dict[str, str]
) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(stage_environment)
    environment["PYTHONUNBUFFERED"] = "1"
    environment["ARTIFACTFIT_NETWORK_POLICY"] = "ALLOW" if network_allowed else "DENY"
    if not network_allowed:
        environment.update(
            {
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "PIP_NO_INDEX": "1",
                "WANDB_MODE": "offline",
            }
        )
    return environment


def _execute_stage(
    key: str,
    spec: StageSpec,
    *,
    run_directory: Path,
    loaded: LoadedContract,
    inspection: RepositoryInspection,
    workspace: Path,
    machine: StageMachine,
    policy: PolicyDecision,
) -> tuple[str, bool]:
    stage = STAGE_BY_KEY[key]
    stage_workspace = _contained_path(workspace, spec.working_directory)
    input_hashes = command_file_hashes(list(spec.command), stage_workspace)
    stdout_temp = run_directory / f".{key}.stdout.tmp"
    stderr_temp = run_directory / f".{key}.stderr.tmp"
    target = loaded.contract.target
    limits = ProcessLimits(
        max_wall_time_s=min(spec.timeout, target.max_wall_time),
        max_ram_bytes=int(target.max_system_ram_gb * GIB),
        max_gpu_bytes=int(target.max_vram_gb * GIB),
        max_workspace_growth_bytes=int(target.max_workspace_growth_gb * GIB),
        stdout_limit_bytes=loaded.contract.runtime.stdout_limit_bytes,
        stderr_limit_bytes=loaded.contract.runtime.stderr_limit_bytes,
        sample_interval_s=loaded.contract.runtime.sample_interval_ms / 1000,
        require_clean_exit=loaded.contract.runtime.require_clean_exit,
    )
    started_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    process_result = run_bounded_process(
        spec.command,
        working_directory=stage_workspace,
        environment=_runtime_environment(
            loaded.contract.runtime.network_allowed,
            spec.environment,
        ),
        limits=limits,
        stdout_path=stdout_temp,
        stderr_path=stderr_temp,
    )
    ended_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    predicates_pass, predicates = _predicates_pass(
        spec, workspace, process_result, stdout_temp, stderr_temp
    )
    if process_result.blocking_state:
        machine.block(BlockingState(process_result.blocking_state), "runtime enforcement")
        verdict = process_result.blocking_state
        passed = False
    elif process_result.cleanup_status != "CLEAN":
        machine.block(BlockingState.CLEANUP_FAILED, "residual process or GPU resource")
        verdict = BlockingState.CLEANUP_FAILED.value
        passed = False
    elif not predicates_pass:
        state = (
            BlockingState.EXECUTION_FAILED
            if process_result.exit_code not in (0, None)
            else BlockingState.INSUFFICIENT_EVIDENCE
        )
        machine.block(state, "one or more success predicates failed")
        verdict = state.value
        passed = False
    else:
        machine.pass_stage(stage)
        verdict = stage.value
        passed = True
    outputs = _observed_outputs(workspace, spec.expected_outputs)
    receipt = _base_receipt(
        loaded,
        inspection,
        workspace,
        stage,
        machine,
        policy,
        final_verdict=verdict,
        cleanup_status=process_result.cleanup_status,
    )
    receipt.update(
        {
            "command": list(spec.command),
            "command_input_hashes": input_hashes,
            "environment": dict(spec.environment),
            "network_policy": (
                "CONTRACT_EXPLICIT_ALLOW"
                if loaded.contract.runtime.network_allowed
                else "DEFAULT_DENY_SOFT"
            ),
            "start_time": started_at,
            "end_time": ended_at,
            "exit_code": process_result.exit_code,
            "timeout": process_result.blocking_state == BlockingState.BUDGET_TIME_EXCEEDED.value,
            "wall_clock_seconds": process_result.wall_clock_seconds,
            "termination_latency_seconds": (
                max(0.0, process_result.wall_clock_seconds - process_result.trace[-1].elapsed_s)
                if process_result.trace
                else None
            ),
            "peak_gpu_bytes": process_result.peak_gpu_bytes,
            "peak_ram_bytes": process_result.peak_rss_bytes,
            "workspace_delta_bytes": process_result.workspace_delta_bytes,
            "enforcement_mode": process_result.enforcement,
            "stdout_observed_bytes": process_result.stdout_observed_bytes,
            "stderr_observed_bytes": process_result.stderr_observed_bytes,
            "stdout_written_bytes": process_result.stdout_written_bytes,
            "stderr_written_bytes": process_result.stderr_written_bytes,
            "stdout_truncated": process_result.stdout_truncated,
            "stderr_truncated": process_result.stderr_truncated,
            "expected_outputs": list(spec.expected_outputs),
            "observed_outputs": outputs,
            "success_predicates": predicates,
            "residual_pids": list(process_result.residual_pids),
            "gpu_release_verified": process_result.gpu_release_verified,
            "download_measurement": "DOWNLOAD_BYTES_UNMEASURED",
        }
    )
    path = write_stage_receipt(
        run_directory / f"{key}_{stage.value}",
        receipt=receipt,
        command={
            "argv": list(spec.command),
            "working_directory": str(stage_workspace),
            "environment_overrides": dict(spec.environment),
            "input_file_hashes": input_hashes,
        },
        transform_diff=policy.as_dict(),
        workspace=workspace,
        files_to_hash=loaded.contract.evidence.files_to_hash,
        process_result=process_result,
        stdout_source=stdout_temp,
        stderr_source=stderr_temp,
    )
    return str(path), passed


def run_contract(
    contract_path: str | Path,
    *,
    workspace: str | Path,
    receipt_root: str | Path,
    run_label: str | None = None,
) -> RunResult:
    loaded = load_contract(contract_path)
    resolved_workspace = Path(workspace).resolve()
    root = Path(receipt_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    label = run_label or loaded.contract.artifact.id
    run_directory = root / f"{_timestamp_id()}_{label}"
    run_directory.mkdir(parents=False, exist_ok=False)
    inspection = inspect_repository(resolved_workspace, loaded.contract.artifact)
    policy = evaluate_transforms(loaded.contract)
    machine = StageMachine()
    receipt_paths: list[str] = []

    if not inspection.exists or not inspection.commit_matches:
        machine.block(BlockingState.INSUFFICIENT_EVIDENCE, "workspace missing or commit mismatch")
        receipt_paths.append(
            _write_internal_receipt(
                run_directory,
                "P0",
                loaded=loaded,
                inspection=inspection,
                workspace=resolved_workspace,
                machine=machine,
                policy=policy,
                verdict=BlockingState.INSUFFICIENT_EVIDENCE.value,
                observed_outputs=[inspection.as_dict()],
            )
        )
    else:
        machine.pass_stage(Stage.P0_DISCOVERED)
        receipt_paths.append(
            _write_internal_receipt(
                run_directory,
                "P0",
                loaded=loaded,
                inspection=inspection,
                workspace=resolved_workspace,
                machine=machine,
                policy=policy,
                verdict=Stage.P0_DISCOVERED.value,
                observed_outputs=[inspection.as_dict()],
            )
        )
    if not machine.terminal:
        if not inspection.license_accepted:
            machine.block(BlockingState.BLOCKED_LICENSE, inspection.reason)
            verdict = BlockingState.BLOCKED_LICENSE.value
        elif policy.final_class is TransformClass.CONTRACT_VIOLATION:
            machine.block(BlockingState.CONTRACT_VIOLATION, "forbidden transform requested")
            verdict = BlockingState.CONTRACT_VIOLATION.value
        elif policy.final_class is TransformClass.REVIEW_REQUIRED:
            machine.block(BlockingState.INSUFFICIENT_EVIDENCE, "transform requires human review")
            verdict = BlockingState.INSUFFICIENT_EVIDENCE.value
        else:
            machine.pass_stage(Stage.P1_POLICY_ACCEPTED)
            verdict = Stage.P1_POLICY_ACCEPTED.value
        receipt_paths.append(
            _write_internal_receipt(
                run_directory,
                "P1",
                loaded=loaded,
                inspection=inspection,
                workspace=resolved_workspace,
                machine=machine,
                policy=policy,
                verdict=verdict,
                observed_outputs=[inspection.as_dict(), policy.as_dict()],
            )
        )

    recorded_commands: dict[str, list[str]] = {
        "P0": ["artifactfit:internal:p0"],
        "P1": ["artifactfit:internal:p1"],
    }
    recorded_command_file_hashes: dict[str, list[dict[str, object]]] = {
        "P0": [],
        "P1": [],
    }
    for index in range(2, 9):
        key = f"P{index}"
        spec = loaded.contract.stages.get(key)
        if spec is None or not spec.enabled:
            break
        recorded_commands[key] = list(spec.command)
        stage_workspace = _contained_path(resolved_workspace, spec.working_directory)
        recorded_command_file_hashes[key] = command_file_hashes(list(spec.command), stage_workspace)
    if not machine.terminal:
        for index in range(2, 9):
            key = f"P{index}"
            spec = loaded.contract.stages.get(key)
            if spec is None or not spec.enabled:
                break
            receipt_path, passed = _execute_stage(
                key,
                spec,
                run_directory=run_directory,
                loaded=loaded,
                inspection=inspection,
                workspace=resolved_workspace,
                machine=machine,
                policy=policy,
            )
            receipt_paths.append(receipt_path)
            if not passed:
                break

    observed = machine.highest_stage.value if machine.highest_stage else None
    blocking = machine.blocking_state.value if machine.blocking_state else None
    final_verdict = blocking or observed or BlockingState.INSUFFICIENT_EVIDENCE.value
    summary = {
        **FIXED_NON_CLAIMS,
        "tool_version": __version__,
        "host": socket.gethostname(),
        "contract_path": str(loaded.source_path),
        "contract_hash": loaded.contract_hash,
        "workspace": str(resolved_workspace),
        "artifact_id": loaded.contract.artifact.id,
        "repository": loaded.contract.artifact.repository,
        "declared_commit": loaded.contract.artifact.commit,
        "observed_commit": inspection.observed_commit,
        "observed_highest_stage": observed,
        "blocking_state": blocking,
        "final_verdict": final_verdict,
        "history": list(machine.history),
        "recorded_commands": recorded_commands,
        "recorded_command_file_hashes": recorded_command_file_hashes,
        "receipt_paths": receipt_paths,
        "environment": capture_environment(),
    }
    summary_path = run_directory / "run_summary.json"
    _write_json(summary_path, summary)
    return RunResult(
        run_directory=str(run_directory),
        run_summary=str(summary_path),
        observed_highest_stage=observed,
        blocking_state=blocking,
        final_verdict=final_verdict,
        receipt_paths=tuple(receipt_paths),
    )
