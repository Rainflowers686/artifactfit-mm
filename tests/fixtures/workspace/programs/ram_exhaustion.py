import time

chunks = []
for _ in range(64):
    chunks.append(bytearray(4 * 1024 * 1024))
    time.sleep(0.05)
time.sleep(5)
