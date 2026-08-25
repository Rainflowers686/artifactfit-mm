from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path
from typing import Any

import pynvml  # type: ignore[import-untyped]


def process_gpu_bytes(handles: list[Any], pid: int) -> int | None:
    total = 0
    seen: set[tuple[int, int]] = set()
    for device_index, handle in enumerate(handles):
        processes: list[Any] = []
        for getter_name in (
            "nvmlDeviceGetComputeRunningProcesses_v3",
            "nvmlDeviceGetComputeRunningProcesses",
            "nvmlDeviceGetGraphicsRunningProcesses_v3",
            "nvmlDeviceGetGraphicsRunningProcesses",
        ):
            getter = getattr(pynvml, getter_name, None)
            if getter is None:
                continue
            try:
                processes.extend(getter(handle))
            except Exception:
                continue
        for process in processes:
            if int(process.pid) != pid or (device_index, pid) in seen:
                continue
            used = getattr(process, "usedGpuMemory", None)
            if used is None or used == getattr(pynvml, "NVML_VALUE_NOT_AVAILABLE", None):
                return None
            total += int(used)
            seen.add((device_index, pid))
    return total


def device_used_bytes(handles: list[Any]) -> int:
    return sum(int(pynvml.nvmlDeviceGetMemoryInfo(handle).used) for handle in handles)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pid_file", type=Path)
    parser.add_argument("stop_file", type=Path)
    parser.add_argument("trace_file", type=Path)
    parser.add_argument("summary_file", type=Path)
    parser.add_argument("--interval", type=float, default=0.05)
    parser.add_argument(
        "--fallback-device-delta",
        action="store_true",
        help="Use device-used delta from a pre-process baseline when WDDM hides per-process bytes.",
    )
    args = parser.parse_args()
    pynvml.nvmlInit()
    handles = [
        pynvml.nvmlDeviceGetHandleByIndex(index) for index in range(pynvml.nvmlDeviceGetCount())
    ]
    start = time.monotonic()
    baseline_device_bytes = device_used_bytes(handles)
    peak: int | None = 0
    observed_pid: int | None = None
    rows: list[tuple[float, int | None, int | None]] = []
    unavailable = False
    reference_mode = "NVML_PROCESS_MEMORY"
    while not args.stop_file.exists():
        used: int | None
        if args.pid_file.exists():
            try:
                observed_pid = int(args.pid_file.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                observed_pid = None
        if observed_pid is None:
            baseline_device_bytes = min(baseline_device_bytes, device_used_bytes(handles))
            used = 0
        else:
            used = process_gpu_bytes(handles, observed_pid)
        if used is None:
            unavailable = True
            if args.fallback_device_delta:
                used = max(0, device_used_bytes(handles) - baseline_device_bytes)
                reference_mode = "NVML_DEVICE_USED_DELTA_FROM_PRE_PROCESS_BASELINE"
            else:
                peak = None
        if used is not None and peak is not None:
            peak = max(peak, used)
        rows.append((time.monotonic() - start, observed_pid, used))
        time.sleep(args.interval)
    pynvml.nvmlShutdown()
    args.trace_file.parent.mkdir(parents=True, exist_ok=True)
    with args.trace_file.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["elapsed_s", "pid", "gpu_bytes"])
        writer.writerows(rows)
    args.summary_file.write_text(
        json.dumps(
            {
                "independent_sampler": "separate-process NVML",
                "pid": observed_pid,
                "peak_gpu_bytes": peak,
                "sample_count": len(rows),
                "process_memory_unavailable": unavailable,
                "reference_mode": reference_mode,
                "baseline_device_bytes": baseline_device_bytes,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
