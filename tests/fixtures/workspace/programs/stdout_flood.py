import sys

for _ in range(2048):
    sys.stdout.write("X" * 1024)
sys.stdout.flush()
print("\nFLOOD_COMPLETE", file=sys.stderr)
