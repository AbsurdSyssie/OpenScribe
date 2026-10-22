from __future__ import annotations

import json
import stat
from datetime import datetime, timezone

from app.services import deepgram_diagnostics as diagnostics


def test_closed_schema_emits_visible_safe_json_and_file_sink_only_records(tmp_path, capsys):
    diagnostics.reset_for_tests()
    path = tmp_path / "logs" / "deepgram-live.jsonl"
    diagnostics.configure_file_sink(str(path))

    diagnostic_id = "a" * 32
    diagnostics.emit("provider_connect", diagnostic_id=diagnostic_id, phase="connect", outcome="success", duration_ms=14)
    diagnostics.emit("provider_connect", diagnostic_id=diagnostic_id, phase="connect", outcome="success", token="SYNTHETIC_SECRET")

    lines = path.read_text().splitlines()
    assert len(lines) == 1
    file_record = json.loads(lines[0])
    assert {key: value for key, value in file_record.items() if key != "timestamp"} == {
        "diagnostic_id": diagnostic_id,
        "duration_ms": 14,
        "event": "provider_connect",
        "outcome": "success",
        "phase": "connect",
    }
    output = capsys.readouterr().out
    stdout_records = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
    assert len(stdout_records) == 1
    assert stdout_records[0] == file_record
    timestamp = stdout_records[0]["timestamp"]
    parsed_timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    assert parsed_timestamp.tzinfo == timezone.utc
    assert parsed_timestamp.microsecond % 1000 == 0
    assert "SYNTHETIC_SECRET" not in output
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    diagnostics.reset_for_tests()


def test_file_sink_rotation_is_bounded_and_preserves_private_permissions(tmp_path, monkeypatch):
    diagnostics.reset_for_tests()
    monkeypatch.setattr(diagnostics, "MAX_BYTES", 350)
    path = tmp_path / "logs" / "deepgram-live.jsonl"
    diagnostics.configure_file_sink(str(path))
    for index in range(30):
        diagnostics.emit("checkpoint", diagnostic_id=f"{index:032x}", phase="checkpoint", outcome="success", window_no=index)

    files = [path, path.with_name(path.name + ".1"), path.with_name(path.name + ".2")]
    existing = [candidate for candidate in files if candidate.exists()]
    assert len(existing) == 3
    assert sum(candidate.stat().st_size for candidate in existing) <= 3 * diagnostics.MAX_BYTES
    assert all(stat.S_IMODE(candidate.stat().st_mode) == 0o600 for candidate in existing)
    diagnostics.reset_for_tests()


def test_unsafe_file_target_is_rejected_without_exposing_path_or_error(tmp_path, capsys):
    diagnostics.reset_for_tests()
    target = tmp_path / "private-target"
    target.write_text("SYNTHETIC_FILE_SENTINEL")
    target.chmod(0o644)

    diagnostics.configure_file_sink(str(target))

    output = capsys.readouterr().out
    assert output.count("deepgram_live_file_log_unavailable") == 1
    assert str(target) not in output
    assert "SYNTHETIC_FILE_SENTINEL" not in output
    assert target.read_text() == "SYNTHETIC_FILE_SENTINEL"
    diagnostics.reset_for_tests()


def test_file_sink_setup_is_idempotent_and_never_accepts_symlinks(tmp_path, capsys):
    diagnostics.reset_for_tests()
    logs = tmp_path / "logs"
    logs.mkdir(mode=0o700)
    path = logs / "deepgram-live.jsonl"
    real = logs / "real.jsonl"
    real.write_text("")
    path.symlink_to(real)

    diagnostics.configure_file_sink(str(path))
    diagnostics.configure_file_sink(str(path))

    output = capsys.readouterr().out
    assert output.count("deepgram_live_file_log_unavailable") == 1
    assert real.read_text() == ""
    diagnostics.reset_for_tests()


def test_sink_write_failure_is_nonfatal_and_redacted(tmp_path, capsys):
    diagnostics.reset_for_tests()
    path = tmp_path / "logs" / "deepgram-live.jsonl"
    diagnostics.configure_file_sink(str(path))
    assert diagnostics._FILE_HANDLER is not None
    diagnostics._FILE_HANDLER.stream.close()

    diagnostics.emit(
        "checkpoint", diagnostic_id="b" * 32, phase="checkpoint", outcome="success",
        window_no=2,
    )

    captured = capsys.readouterr()
    assert captured.out.count("deepgram_live_file_log_unavailable") == 1
    assert '"event":"checkpoint"' in captured.out
    assert captured.err == ""
    diagnostics.emit("checkpoint", diagnostic_id="b" * 32, phase="checkpoint", outcome="success")
    assert capsys.readouterr().out.count("deepgram_live_file_log_unavailable") == 0
    diagnostics.reset_for_tests()


def test_invalid_unhashable_event_is_silently_dropped(capsys):
    diagnostics.reset_for_tests()
    diagnostics.emit([], diagnostic_id="c" * 32)
    assert capsys.readouterr().out == ""
    diagnostics.reset_for_tests()
