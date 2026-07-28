from pathlib import Path


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
