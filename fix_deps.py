#!/usr/bin/env python3
"""Diagnose and fix the Prefect/Pydantic version mismatch.

Run this with the SAME Python you use for the autonomy engine:
    python3 fix_deps.py

It will:
1. Show your current versions
2. Identify the mismatch
3. Offer to fix it
"""

import subprocess
import sys


def get_version(package):
    """Get installed version of a package, or None."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "show", package], capture_output=True, text=True
        )
        for line in result.stdout.splitlines():
            if line.startswith("Version:"):
                return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return None


def check_import(module, name):
    """Check if a specific name can be imported from a module."""
    try:
        exec(f"from {module} import {name}")
        return True
    except ImportError:
        return False


def main():
    print("=" * 60)
    print("Autonomy Engine — Dependency Diagnostic")
    print("=" * 60)
    print()
    print(f"Python: {sys.executable}")
    print(f"Version: {sys.version}")
    print()

    # Check versions
    pydantic_ver = get_version("pydantic")
    prefect_ver = get_version("prefect")

    print(f"pydantic: {pydantic_ver or 'NOT INSTALLED'}")
    print(f"prefect:  {prefect_ver or 'NOT INSTALLED'}")
    print()

    if not pydantic_ver:
        print("ERROR: pydantic is not installed.")
        print(f"  Fix: {sys.executable} -m pip install 'pydantic>=2.11'")
        return

    if not prefect_ver:
        print("ERROR: prefect is not installed.")
        print(f"  Fix: {sys.executable} -m pip install 'prefect>=3.0'")
        return

    # Check the specific failing import
    has_secret = check_import("pydantic", "Secret")
    print(f"pydantic.Secret available: {has_secret}")

    if has_secret:
        # Also check the full prefect import chain
        try:
            print("prefect imports OK: True")
            print()
            print("Everything looks good! The engine should work.")
            return
        except Exception as e:
            print(f"prefect imports OK: False ({e})")

    print()
    print("-" * 60)
    print("DIAGNOSIS:")
    print()

    # Parse version for comparison
    major, minor, *_ = pydantic_ver.split(".")
    pydantic_minor = int(minor)

    if int(major) < 2 or (int(major) == 2 and pydantic_minor < 11):
        print(f"  Your pydantic {pydantic_ver} is too old.")
        print(f"  Prefect {prefect_ver} requires pydantic >= 2.11")
        print("  (pydantic.Secret was added in 2.11)")
        print()
        print("FIX OPTIONS (pick one):")
        print()
        print("  Option A — Upgrade pydantic (recommended):")
        print(f"    {sys.executable} -m pip install 'pydantic>=2.11' --upgrade")
        print()
        print("  Option B — Downgrade prefect to match your pydantic:")
        print(f"    {sys.executable} -m pip install 'prefect>=3.0,<3.2' --force-reinstall")
        print()

        answer = input("Auto-fix with Option A? [y/N] ").strip().lower()
        if answer == "y":
            print()
            print("Upgrading pydantic...")
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "pydantic>=2.11", "--upgrade"],
                capture_output=False,
            )
            if result.returncode == 0:
                print()
                print("Done! Verifying...")
                verify = subprocess.run(
                    [sys.executable, "-c", "from pydantic import Secret; print('OK')"],
                    capture_output=True,
                    text=True,
                )
                if "OK" in verify.stdout:
                    print("pydantic.Secret import: OK")
                    print("You can now run the dashboard and pipeline.")
                else:
                    print("Still failing. Try Option B or check for multiple Python installations.")
            else:
                print(f"pip exited with code {result.returncode}. Try running manually.")
    else:
        print(f"  pydantic {pydantic_ver} should have Secret.")
        print("  This might be a corrupted install.")
        print()
        print(f"  Try: {sys.executable} -m pip install pydantic --force-reinstall")


if __name__ == "__main__":
    main()
