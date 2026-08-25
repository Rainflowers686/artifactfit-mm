from __future__ import annotations

import os
import subprocess
import threading
import time
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, BinaryIO

import psutil
import pynvml  # type: ignore[import-untyped]

from artifactfit_mm.resources.windows_gpu import WindowsGpuProcessMemorySampler
from artifactfit_mm.stages.machine import BlockingState

GIB = 1024**3


@dataclass(frozen=True, slots=True)
class ProcessLimits:
    max_wall_time_s: float
    max_ram_bytes: int
    max_gpu_bytes: int
    max_workspace_growth_bytes: int
    stdout_limit_bytes: int
    stderr_limit_bytes: int
    sample_interval_s: float = 0.1
    require_clean_exit: bool = True


@dataclass(frozen=True, slots=True)
class ResourceSample:
    elapsed_s: float
    process_count: int
    rss_bytes: int
    gpu_bytes: int | None
    workspace_bytes: int


@dataclass(frozen=True, slots=True)
class ProcessResult:
    command: tuple[str, ...]
    working_directory: str
    exit_code: int | None
    blocking_state: str | None
    wall_clock_seconds: float
    peak_rss_bytes: int
    peak_gpu_bytes: int | None
    workspace_start_bytes: int
    workspace_end_bytes: int
    workspace_delta_bytes: int
    stdout_written_bytes: int
    stderr_written_bytes: int
    stdout_observed_bytes: int
    stderr_observed_bytes: int
    stdout_truncated: bool
    stderr_truncated: bool
    cleanup_status: str
    residual_pids: tuple[int, ...]
    gpu_release_verified: bool | None
    enforcement: dict[str, str]
    trace: tuple[ResourceSample, ...]

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["trace"] = [asdict(item) for item in self.trace]
        return value


class _NvmlSampler:
    def __init__(self) -> None:
        self.available = False
        self.reason = "NVML_UNAVAILABLE"
        self._handles: list[Any] = []
        try:
            pynvml.nvmlInit()
            self._handles = [
                pynvml.nvmlDeviceGetHandleByIndex(index)
                for index in range(pynvml.nvmlDeviceGetCount())
            ]
            self.available = True
            self.reason = "NVML_PROCESS_ACCOUNTING"
        except Exception as exc:
            self.reason = f"NVML_INIT_FAILED:{type(exc).__name__}"

    def sample(self, pids: set[int]) -> int | None:
        if not self.available:
            return None
        total = 0
        seen: set[tuple[int, int]] = set()
        try:
            for device_index, handle in enumerate(self._handles):
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
                    pid = int(process.pid)
                    key = (device_index, pid)
                    if pid not in pids or key in seen:
                        continue
                    used = getattr(process, "usedGpuMemory", None)
                    unavailable = getattr(pynvml, "NVML_VALUE_NOT_AVAILABLE", None)
                    if used is None or used == unavailable or int(used) < 0:
                        self.available = False
                        self.reason = "NVML_PROCESS_MEMORY_NOT_AVAILABLE"
                        return None
                    total += int(used)
                    seen.add(key)
            return total
        except Exception as exc:
            self.available = False
            self.reason = f"NVML_SAMPLE_FAILED:{type(exc).__name__}"
            return None

    def close(self) -> None:
        if self._handles:
            with suppress(Exception):
                pynvml.nvmlShutdown()


@dataclass(slots=True)
class _StreamCounter:
    observed: int = 0
    written: int = 0
    truncated: bool = False


def _drain_stream(stream: BinaryIO, destination: Path, limit: int, counter: _StreamCounter) -> None:
    with destination.open("wb") as output:
        while True:
            chunk = stream.read(65_536)
            if not chunk:
                break
            counter.observed += len(chunk)
            remaining = max(0, limit - counter.written)
            if remaining:
                data = chunk[:remaining]
                output.write(data)
                counter.written += len(data)
            if len(chunk) > remaining:
                counter.truncated = True


def workspace_size(path: Path) -> int:
    total = 0
    try:
        for root, directories, files in os.walk(path):
            directories[:] = [name for name in directories if name not in {".git", ".venv"}]
            for name in files:
                try:
                    total += (Path(root) / name).stat().st_size
                except OSError:
                    continue
    except OSError:
        return total
    return total


def _tree_pids(root_pid: int, known: set[int]) -> set[int]:
    result = set(known)
    try:
        root = psutil.Process(root_pid)
        result.add(root_pid)
        result.update(child.pid for child in root.children(recursive=True))
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    return result


def _tree_rss(pids: set[int]) -> int:
    total = 0
    for pid in pids:
        try:
            total += psutil.Process(pid).memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return total


def _terminate_pids(pids: set[int], root_pid: int) -> tuple[int, ...]:
    processes: list[psutil.Process] = []
    for pid in sorted(pids, key=lambda item: item == root_pid):
        try:
            processes.append(psutil.Process(pid))
        except psutil.NoSuchProcess:
            continue
    for process in processes:
        try:
            process.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    _, alive = psutil.wait_procs(processes, timeout=2.0)
    for process in alive:
        try:
            process.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    _, alive = psutil.wait_procs(alive, timeout=2.0)
    return tuple(sorted(process.pid for process in alive if process.is_running()))


def run_bounded_process(
    command: list[str],
    *,
    working_directory: Path,
    environment: dict[str, str],
    limits: ProcessLimits,
    stdout_path: Path,
    stderr_path: Path,
) -> ProcessResult:
    """Run one command while polling its process tree and terminating on budget breach."""
    if not command or any(not isinstance(part, str) or not part for part in command):
        raise ValueError("command must be a non-empty argv list")
    cwd = working_directory.resolve()
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    start_workspace = workspace_size(cwd)
    start = time.monotonic()
    popen_options: dict[str, Any] = {
        "cwd": str(cwd),
        "env": environment,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "shell": False,
    }
    if os.name == "nt":
        popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_options["start_new_session"] = True
    process = subprocess.Popen(command, **popen_options)
    assert process.stdout is not None
    assert process.stderr is not None
    stdout_counter = _StreamCounter()
    stderr_counter = _StreamCounter()
    stdout_thread = threading.Thread(
        target=_drain_stream,
        args=(process.stdout, stdout_path, limits.stdout_limit_bytes, stdout_counter),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_drain_stream,
        args=(process.stderr, stderr_path, limits.stderr_limit_bytes, stderr_counter),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()

    nvml = _NvmlSampler()
    windows_gpu = WindowsGpuProcessMemorySampler()
    known_pids: set[int] = {process.pid}
    trace: list[ResourceSample] = []
    peak_rss = 0
    peak_gpu: int | None = 0 if nvml.available else None
    workspace_now = start_workspace
    last_workspace_sample = start - 2.0
    blocking_state: BlockingState | None = None

    try:
        while True:
            elapsed = time.monotonic() - start
            known_pids = _tree_pids(process.pid, known_pids)
            live_pids = {pid for pid in known_pids if psutil.pid_exists(pid)}
            rss = _tree_rss(live_pids)
            nvml_gpu = nvml.sample(live_pids)
            windows_gpu_value = windows_gpu.sample(live_pids)
            if nvml_gpu is None or (
                windows_gpu_value is not None and windows_gpu_value > 0 and nvml_gpu == 0
            ):
                gpu = windows_gpu_value
            else:
                gpu = nvml_gpu
            if time.monotonic() - last_workspace_sample >= 0.5:
                workspace_now = workspace_size(cwd)
                last_workspace_sample = time.monotonic()
            peak_rss = max(peak_rss, rss)
            if gpu is not None:
                peak_gpu = max(peak_gpu or 0, gpu)
            trace.append(
                ResourceSample(
                    elapsed_s=round(elapsed, 6),
                    process_count=len(live_pids),
                    rss_bytes=rss,
                    gpu_bytes=gpu,
                    workspace_bytes=workspace_now,
                )
            )
            if elapsed > limits.max_wall_time_s:
                blocking_state = BlockingState.BUDGET_TIME_EXCEEDED
            elif rss > limits.max_ram_bytes:
                blocking_state = BlockingState.BUDGET_RAM_EXCEEDED
            elif gpu is not None and gpu > limits.max_gpu_bytes:
                blocking_state = BlockingState.BUDGET_GPU_EXCEEDED
            elif workspace_now - start_workspace > limits.max_workspace_growth_bytes:
                blocking_state = BlockingState.BUDGET_STORAGE_EXCEEDED
            if blocking_state is not None:
                break
            if process.poll() is not None:
                break
            time.sleep(limits.sample_interval_s)
    finally:
        known_pids = _tree_pids(process.pid, known_pids)
        live_before_cleanup = {pid for pid in known_pids if psutil.pid_exists(pid)}
        residual = _terminate_pids(live_before_cleanup, process.pid) if live_before_cleanup else ()
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            residual = tuple(sorted(set(residual) | {process.pid}))
        stdout_thread.join(timeout=2.0)
        stderr_thread.join(timeout=2.0)
        end_workspace = workspace_size(cwd)
        nvml_after = nvml.sample(set(known_pids))
        windows_after = windows_gpu.sample(set(known_pids))
        if nvml_after is None or (
            windows_after is not None and windows_after > 0 and nvml_after == 0
        ):
            gpu_after = windows_after
        else:
            gpu_after = nvml_after
        gpu_release_verified = None if gpu_after is None else gpu_after == 0
        nvml_reason = nvml.reason
        windows_gpu_reason = windows_gpu.reason
        nvml.close()
        windows_gpu.close()

    if residual:
        cleanup_status = "CLEANUP_FAILED"
        if blocking_state is None:
            blocking_state = BlockingState.CLEANUP_FAILED
    else:
        cleanup_status = "CLEAN"
    enforcement = {
        "wall_time": "HARD_ENFORCED_PROCESS_TREE_TERMINATION",
        "ram": "SOFT_MONITORED_PROCESS_TREE_POLLING",
        "gpu": (
            "SOFT_MONITORED_NVML_OR_WINDOWS_PDH_PROCESS_TREE_POLLING"
            if peak_gpu is not None
            else f"UNAVAILABLE:NVML={nvml_reason};PDH={windows_gpu_reason}"
        ),
        "storage": "SOFT_MONITORED_WORKSPACE_GROWTH_LOWER_BOUND",
        "download": "DOWNLOAD_BYTES_UNMEASURED",
        "network": "DECLARED_DISABLED_NOT_KERNEL_ENFORCED",
        "stdout_stderr": "HARD_ENFORCED_BOUNDED_LOG_WRITES",
    }
    return ProcessResult(
        command=tuple(command),
        working_directory=str(cwd),
        exit_code=process.returncode,
        blocking_state=blocking_state.value if blocking_state else None,
        wall_clock_seconds=round(time.monotonic() - start, 6),
        peak_rss_bytes=peak_rss,
        peak_gpu_bytes=peak_gpu,
        workspace_start_bytes=start_workspace,
        workspace_end_bytes=end_workspace,
        workspace_delta_bytes=end_workspace - start_workspace,
        stdout_written_bytes=stdout_counter.written,
        stderr_written_bytes=stderr_counter.written,
        stdout_observed_bytes=stdout_counter.observed,
        stderr_observed_bytes=stderr_counter.observed,
        stdout_truncated=stdout_counter.truncated,
        stderr_truncated=stderr_counter.truncated,
        cleanup_status=cleanup_status,
        residual_pids=residual,
        gpu_release_verified=gpu_release_verified,
        enforcement=enforcement,
        trace=tuple(trace),
    )
