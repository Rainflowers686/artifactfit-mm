import os
import time

import torch

target_mb = int(os.environ.get("ARTIFACTFIT_GPU_FIXTURE_MB", "256"))
chunks = []
for _ in range(max(1, target_mb // 16)):
    chunks.append(torch.empty((4 * 1024 * 1024,), dtype=torch.float32, device="cuda"))
    torch.cuda.synchronize()
    time.sleep(0.1)
time.sleep(10)
