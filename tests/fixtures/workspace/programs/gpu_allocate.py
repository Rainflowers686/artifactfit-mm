import json
import os
import time
from pathlib import Path

import torch

target_mb = int(os.environ.get("ARTIFACTFIT_GPU_FIXTURE_MB", "256"))
Path("gpu_pid.txt").write_text(str(os.getpid()), encoding="utf-8")
chunks = []
for _ in range(max(1, target_mb // 16)):
    chunks.append(torch.empty((4 * 1024 * 1024,), dtype=torch.float32, device="cuda"))
    torch.cuda.synchronize()
    Path("gpu_reference.json").write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "torch_max_memory_allocated_bytes": torch.cuda.max_memory_allocated(),
                "torch_max_memory_reserved_bytes": torch.cuda.max_memory_reserved(),
            }
        ),
        encoding="utf-8",
    )
    time.sleep(0.1)
time.sleep(10)
