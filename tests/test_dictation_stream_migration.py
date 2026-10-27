"""Downgrade must never reinterpret transient sessions as conversation sessions."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_stream_purpose_downgrade_refuses_existing_dictation(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/g0b1c2d3e4f5_add_stream_purpose.py"
    spec = importlib.util.spec_from_file_location("stream_purpose_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    bind = SimpleNamespace(execute=lambda *_args, **_kwargs: SimpleNamespace(first=lambda: (1,)))
    monkeypatch.setattr(module.op, "get_bind", lambda: bind)
    with pytest.raises(RuntimeError, match="dictation stream sessions"):
        module.downgrade()
