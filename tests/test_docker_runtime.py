from pathlib import Path

import yaml


def _demo_compose() -> dict:
    return yaml.safe_load(Path("docker-compose.demo.yml").read_text())


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


def test_runtime_image_installs_only_runtime_requirements() -> None:
    dockerfile = Path("Dockerfile").read_text()

    assert "pip install -r requirements.txt" in dockerfile
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
