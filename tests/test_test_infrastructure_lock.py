import os
from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_busy_audit_exits_before_destructive_setup(tmp_path):
    """The standalone audit must not touch the database or Redis when busy."""
    lock_path = tmp_path / "openscribe_pytest.lock"
    marker_path = tmp_path / "destructive-call"
    environment = {
        **os.environ,
        "APP_ENV": "test",
        "COOKIE_SECURE_MODE": "auto",
        "HSTS_SOURCE": "app",
        "PYTHONPATH": str(REPO_ROOT),
    }
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "\n".join(
                [
                    "import sys",
                    "from tests import db_utils",
                    "db_utils.TEST_INFRASTRUCTURE_LOCK_PATH = sys.argv[1]",
                    "lock = db_utils.acquire_test_infrastructure_lock()",
                    "print('locked', flush=True)",
                    "sys.stdin.read()",
                    "db_utils.release_test_infrastructure_lock(lock)",
                ]
            ),
            str(lock_path),
        ],
        cwd=REPO_ROOT,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "locked"

        contender = subprocess.run(
            [
                sys.executable,
                "-c",
                "\n".join(
                    [
                        "import sys",
                        "from pathlib import Path",
                        "from tests import db_utils",
                        "db_utils.TEST_INFRASTRUCTURE_LOCK_PATH = sys.argv[1]",
                        "marker = Path(sys.argv[2])",
                        "db_utils.ensure_database_exists = lambda _url: marker.write_text('database')",
                        "from scripts import audit_api_auth as audit",
                        "audit.reset_public_schema = lambda: marker.write_text('schema')",
                        "audit.rate_limit_redis.flushdb = lambda: marker.write_text('redis')",
                        "sys.argv = ['audit_api_auth.py']",
                        "raise SystemExit(audit.main())",
                    ]
                ),
                str(lock_path),
                str(marker_path),
            ],
            cwd=REPO_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )

        assert contender.returncode == 2, contender.stdout + contender.stderr
        assert "already using the shared OpenScribe test infrastructure" in contender.stderr
        assert not marker_path.exists()
    finally:
        if holder.stdin is not None:
            holder.stdin.close()
        holder.wait(timeout=10)
