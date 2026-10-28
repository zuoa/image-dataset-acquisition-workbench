"""Verify a macOS build opens a real native window, then terminate that instance."""

import json
import subprocess
import sys
import time
from pathlib import Path

if sys.platform != "darwin":
    raise SystemExit("Run this check on a macOS host; Windows needs its own native acceptance run.")

import AppKit
import Quartz

root = Path(__file__).resolve().parents[1]
(root / "build").mkdir(exist_ok=True)
with (root / "build/bundle-launch.log").open("w") as output:
    process = subprocess.Popen(
        [
            str(root / "dist/Vannus.app/Contents/MacOS/Vannus"),
            "--workspace",
            str(root / "build/bundle-workspace"),
        ],
        stdout=output,
        stderr=subprocess.STDOUT,
    )
    try:
        window = None
        for _ in range(60):
            if process.poll() is not None:
                break
            windows = Quartz.CGWindowListCopyWindowInfo(
                Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID
            )
            window = next(
                (
                    item
                    for item in windows
                    if item.get("kCGWindowOwnerPID") == process.pid and item.get("kCGWindowLayer") == 0
                ),
                None,
            )
            if window:
                break
            time.sleep(0.25)
        result = {
            "running": process.poll() is None,
            "native_window_found": window is not None,
            "title": str(window.get("kCGWindowName", "")) if window else None,
        }
        (root / "build/bundle-verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps(result, ensure_ascii=False))
        if not window:
            raise SystemExit("Bundle did not open a native window; see build/bundle-launch.log")
    finally:
        application = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(process.pid)
        if application:
            application.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)
