from __future__ import annotations

import csv
import hashlib
import json
import os
import platform
import socket
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil

from artifactfit_mm import __version__
from artifactfit_mm.resources.process import ProcessResult

FIXED_NON_CLAIMS: dict[str, object] = {
    "ENGINEERING_FEASIBILITY_ONLY": True,
    "SCIENTIFIC_RESULT": False,
    "PAPER_CLAIM_REPRODUCED": "not_assessed",
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1_048_576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object in {path}")
    return value


def capture_environment() -> dict[str, Any]:
    return {
        "captured_at": utc_now(),
        "host": socket.gethostname(),
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": sys.version,
        "python_executable": sys.executable,
        "cpu_count_logical": psutil.cpu_count(logical=True),
        "ram_total_bytes": psutil.virtual_memory().total,
        "is_wsl": "microsoft" in platform.release().casefold() or "WSL_INTEROP" in os.environ,
    }


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _write_trace(path: Path, process_result: ProcessResult | None) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["elapsed_s", "process_count", "rss_bytes", "gpu_bytes", "workspace_bytes"])
        if process_result:
            for sample in process_result.trace:
                writer.writerow(
                    [
                        sample.elapsed_s,
                        sample.process_count,
                        sample.rss_bytes,
                        "" if sample.gpu_bytes is None else sample.gpu_bytes,
                        sample.workspace_bytes,
                    ]
                )


def _hash_declared_files(workspace: Path, patterns: list[str]) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    seen: set[Path] = set()
    for pattern in patterns:
        for path in workspace.glob(pattern):
            resolved = path.resolve()
            try:
                resolved.relative_to(workspace.resolve())
            except ValueError:
                records.append({"pattern": pattern, "status": "OUTSIDE_WORKSPACE_REJECTED"})
                continue
            if not resolved.is_file() or resolved in seen:
                continue
            seen.add(resolved)
            records.append(
                {
                    "path": resolved.relative_to(workspace.resolve()).as_posix(),
                    "size": resolved.stat().st_size,
                    "sha256": sha256_file(resolved),
                }
            )
    return records


def _write_manifest(path: Path, records: list[dict[str, object]]) -> None:
    lines = []
    for record in records:
        if "sha256" in record:
            lines.append(f"{record['sha256']}  {record['path']}")
        else:
            lines.append(json.dumps(record, sort_keys=True))
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def write_stage_receipt(
    receipt_directory: Path,
    *,
    receipt: dict[str, Any],
    command: dict[str, Any],
    transform_diff: dict[str, Any],
    workspace: Path,
    files_to_hash: list[str],
    process_result: ProcessResult | None,
    stdout_source: Path | None = None,
    stderr_source: Path | None = None,
) -> Path:
    receipt_directory.mkdir(parents=True, exist_ok=False)
    receipt = {
        **FIXED_NON_CLAIMS,
        "tool_version": __version__,
        "timestamp": utc_now(),
        **receipt,
    }
    _write_json(receipt_directory / "receipt.json", receipt)
    _write_json(receipt_directory / "environment.json", capture_environment())
    _write_json(receipt_directory / "command.json", command)
    _write_json(receipt_directory / "transform_diff.json", transform_diff)
    _write_trace(receipt_directory / "resource_trace.csv", process_result)
    if stdout_source and stdout_source.exists():
        stdout_source.replace(receipt_directory / "stdout.log")
    else:
        (receipt_directory / "stdout.log").write_bytes(b"")
    if stderr_source and stderr_source.exists():
        stderr_source.replace(receipt_directory / "stderr.log")
    else:
        (receipt_directory / "stderr.log").write_bytes(b"")
    hashes = _hash_declared_files(workspace, files_to_hash)
    _write_manifest(receipt_directory / "hash_manifest.txt", hashes)
    lines = [
        f"# ArtifactFit-MM receipt: {receipt.get('stage', 'UNKNOWN')}",
        "",
        "- `ENGINEERING_FEASIBILITY_ONLY: true`",
        "- `SCIENTIFIC_RESULT: false`",
        "- `PAPER_CLAIM_REPRODUCED: not_assessed`",
        f"- Observed highest stage: `{receipt.get('observed_highest_stage')}`",
        f"- Final verdict: `{receipt.get('final_verdict')}`",
        f"- Contract SHA-256: `{receipt.get('contract_hash')}`",
        f"- Cleanup: `{receipt.get('cleanup_status')}`",
        "",
        "This is an engineering preflight receipt. It is not an Artifact Evaluation badge or a scientific reproduction claim.",
    ]
    (receipt_directory / "receipt.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return receipt_directory / "receipt.json"
