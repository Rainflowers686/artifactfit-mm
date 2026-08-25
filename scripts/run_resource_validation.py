from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil

from artifactfit_mm.runners.executor import RunResult, run_contract

CASES = (
    "fixture_gpu_oom",
    "fixture_ram_exhaustion",
    "fixture_timeout",
    "fixture_child_process_leak",
    "fixture_disk_growth",
    "fixture_stdout_flood",
)


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object in {path}")
    return value


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _stage_receipt(result: RunResult, key: str) -> dict[str, Any]:
    receipt = next(Path(path) for path in result.receipt_paths if f"{key}_" in path)
    return _json(receipt)


def _prepare_contract(
    source: Path,
    destination: Path,
    *,
    python_executable: Path,
    gpu: bool,
) -> Path:
    contract = _json(source)
    contract["stages"]["P2"]["command"][0] = str(python_executable)
    if gpu:
        contract["target"]["max_vram_gb"] = 0.45
        contract["target"]["max_system_ram_gb"] = 2
        contract["target"]["max_wall_time"] = 15
        contract["stages"]["P2"]["timeout"] = 15
        contract["stages"]["P2"]["environment"] = {"ARTIFACTFIT_GPU_FIXTURE_MB": "768"}
    _write_json(destination, contract)
    return destination


def _start_independent_sampler(
    product_root: Path, workspace: Path, independent_dir: Path
) -> tuple[subprocess.Popen[bytes], Path, Path]:
    stop_file = workspace / "sampler.stop"
    summary_file = independent_dir / "independent_nvml_summary.json"
    if sys.platform == "win32":
        powershell = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
        if powershell is None:
            raise RuntimeError("no PowerShell executable available for independent WDDM sampling")
        command = [
            powershell,
            "-NoProfile",
            "-File",
            str(product_root / "scripts" / "independent_windows_gpu_sampler.ps1"),
            "-PidFile",
            str(workspace / "gpu_pid.txt"),
            "-StopFile",
            str(stop_file),
            "-TraceFile",
            str(independent_dir / "independent_windows_gpu_trace.csv"),
            "-SummaryFile",
            str(summary_file),
            "-IntervalMilliseconds",
            "25",
        ]
        creationflags = subprocess.CREATE_NO_WINDOW
    else:
        command = [
            sys.executable,
            str(product_root / "scripts" / "independent_nvml_sampler.py"),
            str(workspace / "gpu_pid.txt"),
            str(stop_file),
            str(independent_dir / "independent_nvml_trace.csv"),
            str(summary_file),
            "--interval",
            "0.05",
        ]
        creationflags = 0
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=creationflags,
    )
    return process, stop_file, summary_file


def _finish_sampler(process: subprocess.Popen[bytes], stop_file: Path) -> dict[str, Any]:
    stop_file.write_text("stop\n", encoding="utf-8")
    try:
        stdout, stderr = process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        process.terminate()
        stdout, stderr = process.communicate(timeout=5)
    return {
        "exit_code": process.returncode,
        "stdout": stdout.decode("utf-8", errors="replace"),
        "stderr": stderr.decode("utf-8", errors="replace"),
    }


def run_validation(
    *,
    product_root: Path,
    paper_root: Path,
    probe_python: Path,
    repetitions: int,
) -> dict[str, Any]:
    validation_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    source_workspace = product_root / "tests" / "fixtures" / "workspace"
    contract_source_root = product_root / "tests" / "fixtures" / "contracts"
    tracked_root = paper_root / "05_Resource_Validation"
    contract_root = tracked_root / "contracts"
    receipt_root = tracked_root / "receipts" / validation_id
    independent_root = tracked_root / "independent" / validation_id
    local_root = paper_root / "local_private" / "resource_validation" / validation_id
    for path in (contract_root, receipt_root, independent_root, local_root):
        path.mkdir(parents=True, exist_ok=True)
    contracts: dict[str, Path] = {}
    for case in CASES:
        contracts[case] = _prepare_contract(
            contract_source_root / case / "contract.json",
            contract_root / f"{case}.json",
            python_executable=probe_python if case == "fixture_gpu_oom" else Path(sys.executable),
            gpu=case == "fixture_gpu_oom",
        )

    rows: list[dict[str, Any]] = []
    for case in CASES:
        expected = _json(contract_source_root / case / "expected.json")
        for repetition in range(1, repetitions + 1):
            run_workspace = local_root / f"{case}_{repetition:02d}"
            shutil.copytree(source_workspace, run_workspace)
            independent_dir = independent_root / f"{case}_{repetition:02d}"
            independent_dir.mkdir(parents=True, exist_ok=False)
            sampler: subprocess.Popen[bytes] | None = None
            stop_file: Path | None = None
            sampler_summary: Path | None = None
            if case == "fixture_gpu_oom":
                sampler, stop_file, sampler_summary = _start_independent_sampler(
                    product_root, run_workspace, independent_dir
                )
            try:
                result = run_contract(
                    contracts[case],
                    workspace=run_workspace,
                    receipt_root=receipt_root / f"{case}_{repetition:02d}",
                    run_label=f"{case}_{repetition:02d}",
                )
            finally:
                sampler_process = None
                if sampler is not None and stop_file is not None:
                    sampler_process = _finish_sampler(sampler, stop_file)
                    _write_json(independent_dir / "sampler_process.json", sampler_process)
            stage_receipt = _stage_receipt(result, "P2")
            expected_verdict = expected["final_verdict"]
            observed_verdict = result.final_verdict
            cleanup_pass = stage_receipt["cleanup_status"] == expected["cleanup_status"]
            residual_pass = not stage_receipt.get("residual_pids")
            special_pass = True
            if case == "fixture_stdout_flood":
                special_pass = stage_receipt.get("stdout_truncated") is True
            elif case == "fixture_child_process_leak":
                child_pid = int((run_workspace / "child.pid").read_text(encoding="utf-8"))
                special_pass = not psutil.pid_exists(child_pid)
            independent_peak = None
            relative_error = None
            torch_reference = None
            if (
                case == "fixture_gpu_oom"
                and sampler_summary is not None
                and sampler_summary.exists()
            ):
                independent = _json(sampler_summary)
                independent_peak = independent.get("peak_gpu_bytes")
                product_peak = stage_receipt.get("peak_gpu_bytes")
                if (
                    isinstance(independent_peak, int)
                    and independent_peak > 0
                    and isinstance(product_peak, int)
                ):
                    relative_error = abs(product_peak - independent_peak) / independent_peak
                reference_path = run_workspace / "gpu_reference.json"
                if reference_path.exists():
                    torch_reference = _json(reference_path)
            trace = _json(Path(result.receipt_paths[-1])).get("enforcement_mode", {})
            rows.append(
                {
                    "case": case,
                    "repetition": repetition,
                    "expected_verdict": expected_verdict,
                    "observed_verdict": observed_verdict,
                    "verdict_match": expected_verdict == observed_verdict,
                    "cleanup_pass": cleanup_pass,
                    "residual_pass": residual_pass,
                    "special_pass": special_pass,
                    "run_pass": expected_verdict == observed_verdict
                    and cleanup_pass
                    and residual_pass
                    and special_pass,
                    "wall_clock_seconds": stage_receipt.get("wall_clock_seconds"),
                    "termination_latency_seconds": stage_receipt.get("termination_latency_seconds"),
                    "peak_ram_bytes": stage_receipt.get("peak_ram_bytes"),
                    "peak_gpu_bytes": stage_receipt.get("peak_gpu_bytes"),
                    "independent_peak_gpu_bytes": independent_peak,
                    "gpu_relative_error": relative_error,
                    "torch_reference": torch_reference,
                    "gpu_release_verified": stage_receipt.get("gpu_release_verified"),
                    "enforcement_mode": trace,
                    "run_summary": result.run_summary,
                }
            )

    measurement_contract_value = _json(contract_source_root / "fixture_gpu_oom" / "contract.json")
    measurement_contract_value["artifact"]["id"] = "fixture_gpu_measurement"
    measurement_contract_value["target"].update(
        {
            "gpu_count": 1,
            "max_vram_gb": 2.0,
            "max_system_ram_gb": 2.0,
            "max_wall_time": 20,
        }
    )
    measurement_contract_value["stages"]["P2"].update(
        {
            "command": [str(probe_python), "programs/gpu_allocate.py"],
            "timeout": 20,
            "environment": {
                "ARTIFACTFIT_GPU_FIXTURE_MB": "512",
                "ARTIFACTFIT_GPU_FIXTURE_HOLD_SECONDS": "3",
            },
        }
    )
    measurement_contract = contract_root / "fixture_gpu_measurement.json"
    _write_json(measurement_contract, measurement_contract_value)
    measurement_rows: list[dict[str, Any]] = []
    for repetition in range(1, repetitions + 1):
        run_workspace = local_root / f"fixture_gpu_measurement_{repetition:02d}"
        shutil.copytree(source_workspace, run_workspace)
        independent_dir = independent_root / f"fixture_gpu_measurement_{repetition:02d}"
        independent_dir.mkdir(parents=True, exist_ok=False)
        sampler, stop_file, sampler_summary = _start_independent_sampler(
            product_root, run_workspace, independent_dir
        )
        try:
            result = run_contract(
                measurement_contract,
                workspace=run_workspace,
                receipt_root=receipt_root / f"fixture_gpu_measurement_{repetition:02d}",
                run_label=f"fixture_gpu_measurement_{repetition:02d}",
            )
        finally:
            sampler_process = _finish_sampler(sampler, stop_file)
            _write_json(independent_dir / "sampler_process.json", sampler_process)
        stage_receipt = _stage_receipt(result, "P2")
        independent = _json(sampler_summary)
        independent_peak = independent.get("peak_gpu_bytes")
        product_peak = stage_receipt.get("peak_gpu_bytes")
        relative_error = None
        if (
            isinstance(independent_peak, int)
            and independent_peak > 0
            and isinstance(product_peak, int)
        ):
            relative_error = abs(product_peak - independent_peak) / independent_peak
        reference_path = run_workspace / "gpu_reference.json"
        measurement_rows.append(
            {
                "repetition": repetition,
                "observed_verdict": result.final_verdict,
                "stage_pass": result.observed_highest_stage == "P2_ENVIRONMENT_READY",
                "product_peak_gpu_bytes": product_peak,
                "independent_peak_gpu_bytes": independent_peak,
                "gpu_relative_error": relative_error,
                "torch_reference": _json(reference_path) if reference_path.exists() else None,
                "cleanup_pass": stage_receipt.get("cleanup_status") == "CLEAN",
                "gpu_release_verified": stage_receipt.get("gpu_release_verified"),
                "run_summary": result.run_summary,
            }
        )

    case_pass: dict[str, bool] = {}
    for case in CASES:
        case_rows = [row for row in rows if row["case"] == case]
        case_pass[case] = len(case_rows) == repetitions and all(
            row["run_pass"] for row in case_rows
        )
    gpu_errors = [
        float(row["gpu_relative_error"])
        for row in measurement_rows
        if row["gpu_relative_error"] is not None
    ]
    measurement_execution_pass = len(measurement_rows) == repetitions and all(
        bool(row["stage_pass"])
        and bool(row["cleanup_pass"])
        and row["gpu_release_verified"] is True
        for row in measurement_rows
    )
    metrics = {
        "FEASIBILITY_ONLY": True,
        "SCIENTIFIC_RESULT": False,
        "validation_id": validation_id,
        "repetitions_per_fixture": repetitions,
        "fixture_count": len(CASES),
        "fixture_pass_count": sum(case_pass.values()),
        "case_pass": case_pass,
        "run_count": len(rows),
        "run_pass_count": sum(bool(row["run_pass"]) for row in rows),
        "gpu_measurement_execution_pass": measurement_execution_pass,
        "gpu_measurement_repetition_count": len(gpu_errors),
        "gpu_relative_error_mean": sum(gpu_errors) / len(gpu_errors) if gpu_errors else None,
        "gpu_relative_error_max": max(gpu_errors) if gpu_errors else None,
        "gpu_error_gate_pass": measurement_execution_pass
        and bool(gpu_errors)
        and max(gpu_errors) <= 0.10,
        "gpu_measurement_rows": measurement_rows,
        "all_cleanup_pass": all(bool(row["cleanup_pass"]) for row in rows),
        "all_residual_checks_pass": all(bool(row["residual_pass"]) for row in rows),
        "rows": rows,
    }
    _write_json(tracked_root / "resource_validation_runs.json", rows)
    _write_json(tracked_root / "gpu_measurement_validation_runs.json", measurement_rows)
    _write_json(tracked_root / "resource_metrics.json", metrics)
    with (tracked_root / "resource_matrix.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "case",
                "repetition",
                "expected_verdict",
                "observed_verdict",
                "verdict_match",
                "cleanup_pass",
                "residual_pass",
                "special_pass",
                "run_pass",
                "peak_ram_bytes",
                "peak_gpu_bytes",
                "independent_peak_gpu_bytes",
                "gpu_relative_error",
                "gpu_release_verified",
                "run_summary",
            ],
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("product_root", type=Path)
    parser.add_argument("paper_root", type=Path)
    parser.add_argument("probe_python", type=Path)
    parser.add_argument("--repetitions", type=int, default=5)
    args = parser.parse_args()
    result = run_validation(
        product_root=args.product_root.resolve(),
        paper_root=args.paper_root.resolve(),
        probe_python=args.probe_python.resolve(),
        repetitions=args.repetitions,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
