#!/usr/bin/env python3
"""Run the real synthetic Telehealth UI test in CI's Node environment.

The Python runtime container does not contain Node; this lightweight wrapper
extracts the browser-only fixtures from the pytest test without importing
pytest or requiring test DB/Redis. Thus the same UI assertions run in both
developer pytest and the GitHub Actions host Node step.
"""
from __future__ import annotations

import ast
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TEST = ROOT / "tests/test_telehealth_ui_runtime_js.py"
JS = ROOT / "app/static/js/transcribe"


def fixture_code(filename: str) -> str:
    tree = ast.parse(TEST.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if not isinstance(function, ast.Attribute) or function.attr != "write_text":
            continue
        name = function.value
        if not isinstance(name, ast.BinOp) or not isinstance(name.op, ast.Div):
            continue
        if not isinstance(name.left, ast.Name) or name.left.id != "tmp_path":
            continue
        if not isinstance(name.right, ast.Constant) or name.right.value != filename:
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        if isinstance(node.args[0].value, str):
            return node.args[0].value
    raise SystemExit(f"Could not find the expected synthetic {filename} fixture")


def main() -> None:
    ui = (JS / "telehealthUI.js").read_text(encoding="utf-8")
    imports = {
        "./telehealthRecordingClient.js?v=20261108-final-merge-review": "./fake.mjs",
        "./telehealthCapture.js?v=20261108-system-audio": "./capture.mjs",
        "./telehealthRecoveryJournal.js?v=20261108-final-merge-review": "./fake.mjs",
        "./telehealthDiagnostics.js?v=20261108-audio-scope": "./diagnostics.mjs",
    }
    for original, replacement in imports.items():
        if original not in ui:
            raise SystemExit("Browser module import changed; update CI smoke fixture")
        ui = ui.replace(original, replacement)
    with tempfile.TemporaryDirectory(prefix="openscribe-telehealth-stop-") as temp:
        directory = Path(temp)
        (directory / "ui.mjs").write_text(ui, encoding="utf-8")
        (directory / "fake.mjs").write_text(fixture_code("fake.mjs"), encoding="utf-8")
        (directory / "capture.mjs").write_text(fixture_code("capture.mjs"), encoding="utf-8")
        (directory / "test.mjs").write_text(fixture_code("test.mjs"), encoding="utf-8")
        (directory / "diagnostics.mjs").write_text(
            (JS / "telehealthDiagnostics.js").read_text(encoding="utf-8"), encoding="utf-8"
        )
        subprocess.run(["node", str(directory / "test.mjs")], cwd=directory, check=True)


if __name__ == "__main__":
    main()
