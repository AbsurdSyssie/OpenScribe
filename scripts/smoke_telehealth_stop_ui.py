#!/usr/bin/env python3
"""Run the real synthetic Telehealth UI test in CI's Node environment.

The Python runtime container does not contain Node; this lightweight wrapper
extracts the browser-only fixtures from the pytest test without importing
pytest or requiring test DB/Redis. Thus the same UI assertions run in both
developer pytest and the GitHub Actions host Node step.
"""
from __future__ import annotations

import ast
import re
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


def telehealth_ui_source() -> str:
    ui = (JS / "telehealthUI.js").read_text(encoding="utf-8")
    imports = {
        "./telehealthRecordingClient.js": "./fake.mjs",
        "./telehealthCapture.js": "./capture.mjs",
        "./telehealthRecoveryJournal.js": "./fake.mjs",
        "./telehealthDiagnostics.js": "./diagnostics.mjs",
    }
    for original, replacement in imports.items():
        specifier = rf"(?P<quote>['\"]){re.escape(original)}(?:\?v=[^'\"\s]+)?(?P=quote)"
        ui, count = re.subn(specifier, f"'{replacement}'", ui)
        if count != 1:
            raise SystemExit(f"Expected one Telehealth UI import for {original}; found {count}")
    return ui


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="openscribe-telehealth-stop-") as temp:
        directory = Path(temp)
        (directory / "ui.mjs").write_text(telehealth_ui_source(), encoding="utf-8")
        (directory / "fake.mjs").write_text(fixture_code("fake.mjs"), encoding="utf-8")
        (directory / "capture.mjs").write_text(fixture_code("capture.mjs"), encoding="utf-8")
        (directory / "test.mjs").write_text(fixture_code("test.mjs"), encoding="utf-8")
        (directory / "diagnostics.mjs").write_text(
            (JS / "telehealthDiagnostics.js").read_text(encoding="utf-8"), encoding="utf-8"
        )
        subprocess.run(["node", str(directory / "test.mjs")], cwd=directory, check=True)


if __name__ == "__main__":
    main()
