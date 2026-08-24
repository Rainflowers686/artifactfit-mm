from pathlib import Path

Path("output.txt").write_text("fixture success\n", encoding="utf-8")
print("FIXTURE_SUCCESS")
