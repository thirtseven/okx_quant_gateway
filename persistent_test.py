#!/usr/bin/env python3
import sys
import os
import time
import subprocess

def run_tests():
    """Run test_gateway.py, return (passed, failed, errors, output)."""
    result = subprocess.run(
        [sys.executable, "test_gateway.py"],
        capture_output=True,
        text=True,
        cwd=os.path.dirname(os.path.abspath(__file__)) or "."
    )
    output = result.stdout + result.stderr
    passed = failed = errors = 0
    for line in output.splitlines():
        if "Tests run   :" in line:
            parts = line.split(":")
            if len(parts) >= 2:
                passed = int(parts[1].strip())
        elif "Failures    :" in line:
            parts = line.split(":")
            if len(parts) >= 2:
                failed = int(parts[1].strip())
        elif "Errors      :" in line:
            parts = line.split(":")
            if len(parts) >= 2:
                errors = int(parts[1].strip())
    return passed, failed, errors, output

def main():
    test_file = os.path.join(os.path.dirname(os.path.abspath(__file__)) or ".", "test_gateway.py")
    if not os.path.exists(test_file):
        print(f"ERROR: {test_file} not found")
        return 1

    iteration = 0
    start_time = time.time()
    consecutive_failures = 0

    while True:
        iteration += 1
        elapsed = time.time() - start_time
        print(f"\n{'='*70}")
        print(f"  ITERATION {iteration} | elapsed {elapsed:.0f}s")
        print(f"{'='*70}")

        passed, failed, errors, output = run_tests()
        total_tests = passed + failed + errors

        if failed == 0 and errors == 0:
            print(f"\n{'='*70}")
            print(f"  ALL TESTS PASSED ({passed}/{total_tests})")
            print(f"  Total iterations: {iteration}")
            print(f"  Total time: {time.time() - start_time:.0f}s")
            print(f"{'='*70}")
            return 0
        else:
            consecutive_failures += 1
            last_lines = "\n".join(output.splitlines()[-30:])
            print(f"\n  FAILURE ITERATION {iteration}: {failed} failures, {errors} errors")
            print(f"  Last output:\n{last_lines}")

            wait = 30
            print(f"  Retrying in {wait}s...")
            time.sleep(wait)

if __name__ == "__main__":
    sys.exit(main())
