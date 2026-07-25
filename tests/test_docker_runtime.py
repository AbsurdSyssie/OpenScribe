from pathlib import Path


def test_runtime_image_packages_transcriber_workspace_assets() -> None:
    dockerfile = Path("Dockerfile").read_text()
    dockerignore = Path(".dockerignore").read_text().splitlines()

    assert (
        "COPY transcriber_changes/workspace/static "
        "./transcriber_changes/workspace/static"
    ) in dockerfile
    assert (
        "COPY transcriber_changes/workspace/templates "
        "./transcriber_changes/workspace/templates"
    ) in dockerfile
    assert "!transcriber_changes/workspace/static/**" in dockerignore
    assert "!transcriber_changes/workspace/templates/**" in dockerignore


def test_test_dependencies_split_out_of_runtime_requirements() -> None:
    runtime = Path("requirements.txt").read_text().splitlines()
    dev = Path("requirements-dev.txt").read_text().splitlines()

    assert "-r requirements.txt" in dev
    for pin in ("pytest==8.3.3", "pytest-xdist==3.6.1"):
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
