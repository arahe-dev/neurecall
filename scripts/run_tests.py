"""Run all NeuRecall tests."""

import subprocess
import sys
from pathlib import Path

TESTS = [
    "tests/test_engine.py",
    "tests/test_http.py",
    "tests/test_importers.py",
]

def main():
    root = Path(__file__).resolve().parent.parent
    failures = 0
    for t in TESTS:
        path = root / t
        if not path.exists():
            print(f"SKIP {t} (not found)")
            continue
        print(f"\n{'='*50}")
        print(f"RUNNING {t}")
        print('='*50)
        result = subprocess.run([sys.executable, str(path)])
        if result.returncode != 0:
            failures += 1
    print(f"\n{'='*50}")
    if failures:
        print(f"FAILED {failures}/{len(TESTS)} test suites")
        sys.exit(1)
    print("ALL TEST SUITES PASSED")
    sys.exit(0)

if __name__ == "__main__":
    main()
