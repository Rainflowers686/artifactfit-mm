import subprocess
import sys
import time
from pathlib import Path

child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
Path("child.pid").write_text(str(child.pid), encoding="utf-8")
time.sleep(0.5)
print("PARENT_EXITING")
