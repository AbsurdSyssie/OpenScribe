from pathlib import Path

import pytest
import yaml


def _demo_compose() -> dict:
    return yaml.safe_load(Path("docker-compose.demo.yml").read_text())


def _runtime_compose() -> dict:
    return yaml.safe_load(Path("docker-compose.yml").read_text())


def test_runtime_image_does_not_package_removed_transcriber_prototype() -> None:
    dockerfile = Path("Dockerfile").read_text()
    dockerignore = Path(".dockerignore").read_text()

    assert "transcriber_changes" not in dockerfile
    assert "transcriber_changes" not in dockerignore


def test_test_dependencies_split_out_of_runtime_requirements() -> None:
    runtime = Path("requirements.txt").read_text().splitlines()
    dev = Path("requirements-dev.txt").read_text().splitlines()

    assert "-r requirements.txt" in dev
    for pin in ("pytest==9.0.3", "pytest-xdist==3.6.1"):
        assert pin in dev
        assert pin not in runtime


def test_runtime_requirements_keep_provider_inspection_libraries() -> None:
    runtime = Path("requirements.txt").read_text()

    for package in (
        "openapi-spec-validator",
        "prance",
        "jsonschema",
        "jsonpath-ng",
    ):
        assert package in runtime


def test_runtime_requirements_pin_multipart_idna_and_spacy_model() -> None:
    runtime = Path("requirements.txt").read_text()

    assert "python-multipart==0.0.32" in runtime
    assert "idna==3.18" in runtime
    assert "en_core_web_sm-3.8.0-py3-none-any.whl" in runtime


def test_runtime_uvicorn_caps_websocket_message_size_and_queue() -> None:
    entrypoint = Path("docker/entrypoint.sh").read_text()

    assert "--ws-max-size 65536" in entrypoint
    assert "--ws-max-queue 4" in entrypoint


def test_runtime_image_installs_only_runtime_requirements() -> None:
    dockerfile = Path("Dockerfile").read_text()

    assert "pip install -r requirements.txt" in dockerfile
    assert "pip install --upgrade pip" not in dockerfile
    assert "spacy download" not in dockerfile
    assert "requirements-dev.txt" not in dockerfile


def test_demo_compose_is_isolated_and_publishes_only_local_web() -> None:
    compose = _demo_compose()
    services = compose["services"]

    assert compose["name"] == "openscribe-demo"
    assert services["openscribe"]["ports"] == ["127.0.0.1:8080:8080"]
    for service_name in ("postgres", "redis", "vault", "seed-demo"):
        assert "ports" not in services[service_name]

    assert set(compose["volumes"]) == {
        "postgres_data",
        "redis_data",
        "vault_data",
        "vault_bootstrap",
        "demo_state",
    }


def test_demo_compose_seeds_only_after_healthy_app() -> None:
    compose = _demo_compose()
    services = compose["services"]
    seed = services["seed-demo"]

    assert services["openscribe"]["build"]["context"] == "."
    assert seed["depends_on"]["openscribe"]["condition"] == "service_healthy"
    assert "python scripts/seed_demo.py" in seed["entrypoint"][-1]
    assert "demo_state:/app/.local/demo" in services["openscribe"]["volumes"]
    assert "demo_state:/app/.local/demo" in seed["volumes"]
    assert compose["x-openscribe-environment"]["DEMO_PASSWORD"] == "OpenScribeLocal27"
    assert "DEMO_BOOTSTRAP_ENABLED" not in services["openscribe"]["environment"]
    assert seed["environment"]["DEMO_BOOTSTRAP_ENABLED"] == "true"
    assert "Password:" not in Path("scripts/seed_demo.py").read_text()


def test_consultation_splitting_rollout_gate_defaults_off_in_runtime_and_demo_compose() -> None:
    runtime = _runtime_compose()
    demo = _demo_compose()

    assert runtime["services"]["openscribe"]["environment"]["CONSULTATION_SPLITTING_ENABLED"] == "${CONSULTATION_SPLITTING_ENABLED:-false}"
    assert demo["x-openscribe-environment"]["CONSULTATION_SPLITTING_ENABLED"] == "false"
    assert demo["services"]["openscribe"]["environment"]["CONSULTATION_SPLITTING_ENABLED"] == "false"


def test_worker_settings_are_mapped_in_runtime_compose() -> None:
    environment = _runtime_compose()["services"]["openscribe"]["environment"]
    for name, default in (
        ("CELERY_WORKER_MODE", "combined"),
        ("CELERY_WORKER_CONCURRENCY", "1"),
        ("CELERY_CONTROL_CONCURRENCY", "1"),
        ("CELERY_GENERATION_CONCURRENCY", "2"),
        ("CELERY_INGESTION_CONCURRENCY", "1"),
    ):
        assert environment[name] == "${" + name + ":-" + default + "}"
        assert f"{name}={default}" in Path(".env.example").read_text()


def _worker_shell(script: str, overrides: dict[str, str] | None = None):
    import os
    import subprocess

    environment = {key: value for key, value in os.environ.items() if not key.startswith("CELERY_")}
    environment.update(overrides or {})
    return subprocess.run(
        ["bash", "-c", "set -euo pipefail; source docker/celery-workers.sh; " + script],
        env=environment, capture_output=True, text=True, timeout=10,
    )


def test_combined_and_isolated_worker_commands(tmp_path) -> None:
    import json

    fake = tmp_path / "celery"
    fake.write_text("#!/usr/bin/env python3\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n")
    fake.chmod(0o755)
    for mode, expected in (
        ("combined", {"control,generation,ingestion": "1"}),
        ("isolated", {"control": "1", "generation": "10", "ingestion": "1"}),
    ):
        result = _worker_shell(
            f'validate_celery_workers; start_celery_workers "{fake}"; wait "${{CELERY_WORKER_PIDS[@]}}"',
            {"CELERY_WORKER_MODE": mode, "CELERY_GENERATION_CONCURRENCY": "10"},
        )
        assert result.returncode == 0, result.stderr
        commands = [json.loads(line) for line in result.stdout.splitlines()]
        assert len(commands) == len(expected)
        for command in commands:
            queue = command[command.index("-Q") + 1]
            assert command[command.index("--concurrency") + 1] == expected[queue]
            assert command[command.index("--pool") + 1] == "prefork"
            if mode == "isolated":
                assert command[command.index("--hostname") + 1] == f"{queue}@%h"


def test_invalid_worker_settings_fail_closed() -> None:
    for name in (
        "CELERY_WORKER_CONCURRENCY", "CELERY_CONTROL_CONCURRENCY",
        "CELERY_GENERATION_CONCURRENCY", "CELERY_INGESTION_CONCURRENCY",
    ):
        for value in ("", "0", "-1", "1.5", "abc", " 2", "01"):
            result = _worker_shell("validate_celery_workers", {name: value})
            assert result.returncode != 0
            assert name in result.stderr
    for value in ("", "solo", "ISOLATED"):
        assert _worker_shell("validate_celery_workers", {"CELERY_WORKER_MODE": value}).returncode != 0


def test_celery_prefetch_keeps_one_task_per_slot() -> None:
    from app.celery_app import celery_app

    assert celery_app.conf.worker_prefetch_multiplier == 1


@pytest.mark.parametrize("exiting_child", ["web", "control", "generation", "ingestion", "beat"])
def test_container_supervises_all_isolated_workers(tmp_path, exiting_child) -> None:
    import os
    import subprocess

    # Exercise the real runtime function, replacing only bootstrap and external processes.
    entrypoint = Path("docker/entrypoint.sh").read_text()
    runtime = entrypoint.split("run_runtime() {", 1)[1].split('\ncase "${1:-runtime}"', 1)[0]
    marker = tmp_path / "stopped"
    fake_script = (
        '#!/usr/bin/env bash\ntrap \'echo stopped >> "$MARKER"; exit 0\' TERM\n'
        'child=web\n'
        'if [[ "$(basename "$0")" == celery ]]; then\n'
        '  child=beat\n'
        '  while (( $# )); do if [[ "$1" == -Q ]]; then child="$2"; break; fi; shift; done\n'
        'fi\n'
        'if [[ "$child" == "$EXITING_CHILD" ]]; then sleep 0.3; exit 7; fi\n'
        'while true; do sleep 0.05; done\n'
    )
    for command in ("celery", "uvicorn"):
        fake = tmp_path / command
        fake.write_text(fake_script)
        fake.chmod(0o755)
    environment = {key: value for key, value in os.environ.items() if not key.startswith("CELERY_")}
    environment.update(PATH=f"{tmp_path}:{environment['PATH']}", MARKER=str(marker), EXITING_CHILD=exiting_child, CELERY_WORKER_MODE="isolated")
    result = subprocess.run(
        ["bash", "-c", 'set -euo pipefail; source docker/celery-workers.sh; bootstrap_runtime() { :; }; run_runtime() {' + runtime + '\nrun_runtime'],
        env=environment, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 7, result.stderr
    assert marker.read_text().splitlines() == ["stopped"] * 4  # all four surviving processes


def test_container_signal_stops_workers_beat_and_web(tmp_path) -> None:
    import os
    import subprocess
    import time

    entrypoint = Path("docker/entrypoint.sh").read_text()
    runtime = entrypoint.split("run_runtime() {", 1)[1].split('\ncase "${1:-runtime}"', 1)[0]
    marker = tmp_path / "stopped"
    ready = tmp_path / "ready"
    fake_script = (
        '#!/usr/bin/env bash\ntrap \'echo stopped >> "$MARKER"; exit 0\' TERM\n'
        'echo ready >> "$READY"\nwhile true; do sleep 0.05; done\n'
    )
    for command in ("celery", "uvicorn"):
        fake = tmp_path / command
        fake.write_text(fake_script)
        fake.chmod(0o755)
    environment = {key: value for key, value in os.environ.items() if not key.startswith("CELERY_")}
    environment.update(PATH=f"{tmp_path}:{environment['PATH']}", MARKER=str(marker), READY=str(ready), CELERY_WORKER_MODE="isolated")
    process = subprocess.Popen(
        ["bash", "-c", 'set -euo pipefail; source docker/celery-workers.sh; bootstrap_runtime() { :; }; run_runtime() {' + runtime + '\nrun_runtime'],
        env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if ready.exists() and len(ready.read_text().splitlines()) == 5:
                break
            time.sleep(0.02)
        else:
            raise AssertionError("The fake runtime did not start all five children")
        process.terminate()
        _, stderr = process.communicate(timeout=5)
        assert process.returncode == 143, stderr
        assert marker.read_text().splitlines() == ["stopped"] * 5
    finally:
        if process.poll() is None:
            process.terminate()
            process.communicate(timeout=5)
