import time
from pathlib import Path

with Path("growth.bin").open("wb") as handle:
    for _ in range(32):
        handle.write(b"0" * (512 * 1024))
        handle.flush()
        time.sleep(0.08)
