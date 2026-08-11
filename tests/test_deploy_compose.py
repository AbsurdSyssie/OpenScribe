from __future__ import annotations

import os
from pathlib import Path
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = PROJECT_ROOT / "scripts" / "deploy-compose.sh"


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _clean_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _git(remote, "init", "--quiet", "--bare")
    repository.mkdir()
    _git(repository, "init", "--quiet")
    _git(repository, "config", "user.email", "test@example.invalid")
    _git(repository, "config", "user.name", "OpenScribe test")
    (repository / "tracked.txt").write_text("release\n", encoding="utf-8")
    _git(repository, "add", "tracked.txt")
    _git(repository, "commit", "--quiet", "-m", "test release")
    _git(repository, "remote", "add", "origin", str(remote))
    _git(repository, "push", "--quiet", "--set-upstream", "origin", "HEAD:main")
    return repository


def test_compose_deploy_uses_exact_clean_commit_source(tmp_path):
    repository = _clean_repository(tmp_path)
    release = _git(repository, "rev-parse", "HEAD")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    output = tmp_path / "docker-call.txt"
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n%s\\n%s\\n' \"$APP_RELEASE\" \"$APP_SOURCE_CODE_URL\" \"$*\" > \"$DEPLOY_TEST_OUTPUT\"\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)

    environment = os.environ.copy()
    environment.update(
        {
            "APP_SOURCE_REPOSITORY_URL": "https://source.example/OpenScribe/",
            "DEPLOY_TEST_OUTPUT": str(output),
            "PATH": f"{fake_bin}:{environment['PATH']}",
        }
    )
    subprocess.run([str(DEPLOY_SCRIPT)], cwd=repository, env=environment, check=True)

    assert output.read_text(encoding="utf-8").splitlines() == [
        release,
        f"https://source.example/OpenScribe/tree/{release}",
        "compose --profile runtime up -d --build",
    ]


def test_compose_deploy_refuses_dirty_checkout(tmp_path):
    repository = _clean_repository(tmp_path)
    (repository / "tracked.txt").write_text("changed\n", encoding="utf-8")

    result = subprocess.run(
        [str(DEPLOY_SCRIPT)],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "tracked files have uncommitted changes" in result.stderr


def test_compose_deploy_rejects_unsafe_repository_url(tmp_path):
    repository = _clean_repository(tmp_path)
    environment = os.environ.copy()
    environment["APP_SOURCE_REPOSITORY_URL"] = "javascript:alert(1)"

    result = subprocess.run(
        [str(DEPLOY_SCRIPT)],
        cwd=repository,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "must be an absolute HTTP or HTTPS URL" in result.stderr


def test_compose_deploy_refuses_unpushed_commit(tmp_path):
    repository = _clean_repository(tmp_path)
    (repository / "tracked.txt").write_text("next release\n", encoding="utf-8")
    _git(repository, "add", "tracked.txt")
    _git(repository, "commit", "--quiet", "-m", "unpushed release")

    result = subprocess.run(
        [str(DEPLOY_SCRIPT)],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "Push it first" in result.stderr
