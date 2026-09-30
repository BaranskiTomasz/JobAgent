import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv

# Load real keys from .env / .env.test before setting the fake test default,
# so e2e tests pick up real credentials without manually exporting env vars.
load_dotenv(".env.test", override=False)
load_dotenv(".env", override=False)

# Fall back to a fake key so unit/integration tests run without any .env file.
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-real")
os.environ.setdefault("VOYAGE_API_KEY", "test-voyage-key-not-real")

_TEST_PORT = 8511
os.environ["JOBAGENTWEB_BASE_URL"] = os.environ.get(
    "JOBAGENTWEB_TEST_BASE_URL", f"http://127.0.0.1:{_TEST_PORT}"
)

import pytest
import httpx

import api_client

# JobAgent has no database of its own, every db/repositories/*.py call is a real
# HTTP call to JobAgentWeb (api_client.py). Rather than mock those calls, tests
# run a real JobAgentWeb instance (its own repo/venv, sibling directory) against
# the same isolated jobagentweb_test Postgres its own test suite uses, same
# "hit a real backend, not mocks" philosophy already established there.
_JOBAGENTWEB_REPO = Path(os.environ.get("JOBAGENTWEB_REPO_PATH", Path(__file__).resolve().parent.parent.parent / "JobAgentWeb"))
_JOBAGENTWEB_PYTHON_CANDIDATES = (
    _JOBAGENTWEB_REPO / ".venv" / "bin" / "python",
    _JOBAGENTWEB_REPO / ".venv" / "Scripts" / "python.exe",
)
_JOBAGENTWEB_PYTHON = next(
    (path for path in _JOBAGENTWEB_PYTHON_CANDIDATES if path.is_file()),
    None,
)


@pytest.fixture(scope="session", autouse=True)
def jobagentweb_server():
    if _JOBAGENTWEB_PYTHON is None:
        expected_paths = ", ".join(str(path) for path in _JOBAGENTWEB_PYTHON_CANDIDATES)
        pytest.exit(
            f"JobAgentWeb venv not found at any of: {expected_paths}. Set JOBAGENTWEB_REPO_PATH "
            "or check out JobAgentWeb as a sibling directory with its venv set up.",
            returncode=1,
        )

    env = os.environ.copy()
    env.update({
        "INVITE_CODE": "test-invite-code",  # JobAgentWeb closes registration without one, matches its own conftest.py default
        "SECRET_KEY": "test-only-secret-key-not-real-923nf",  # JobAgentWeb now hard-fails at import without one
        "DISABLE_RATE_LIMIT": "true",  # this suite registers a fresh user per test, far more than a real client
        "POSTGRES_HOST": os.environ.get("POSTGRES_HOST", "10.66.0.1"),
        "POSTGRES_PORT": os.environ.get("POSTGRES_PORT", "5432"),
        "POSTGRES_DB": os.environ.get("POSTGRES_DB", "jobagentweb_test"),
        "POSTGRES_USER": os.environ.get("POSTGRES_USER", "jobagentweb_test"),
        "POSTGRES_PASSWORD": os.environ.get("POSTGRES_PASSWORD", "test_only_pw_923nf"),
        "SESSION_HTTPS_ONLY": "false",
    })
    # A pipe that nobody drains fills up (uvicorn logs a line per request) and the
    # child then blocks on its own stdout write() forever, the whole test run
    # freezes mid-suite with no error, just an unresponsive server. Redirect to a
    # real file instead; the OS handles that without the parent needing to read it.
    log_path = Path(tempfile.gettempdir()) / f"jobagentweb_test_server_{_TEST_PORT}.log"
    log_file = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [str(_JOBAGENTWEB_PYTHON), "-m", "uvicorn", "main:app", "--port", str(_TEST_PORT)],
        cwd=str(_JOBAGENTWEB_REPO), env=env,
        stdout=log_file, stderr=subprocess.STDOUT,
    )

    healthy = False
    for _ in range(75):
        if proc.poll() is not None:
            break
        try:
            if httpx.get(f"http://127.0.0.1:{_TEST_PORT}/healthz", timeout=1).status_code == 200:
                healthy = True
                break
        except httpx.TransportError:
            pass
        time.sleep(0.2)

    if not healthy:
        proc.kill()
        log_file.close()
        output = log_path.read_text(encoding="utf-8", errors="replace")
        pytest.exit(f"JobAgentWeb test server failed to start (log: {log_path}):\n{output[-4000:]}", returncode=1)

    yield

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    log_file.close()


@pytest.fixture(autouse=True)
def _no_static_api_key(monkeypatch):
    """Neutralizes config.JOBAGENT_API_KEY for every test, regardless of what's set
    in this machine's real .env. Without this, api_client.request() unconditionally
    takes the static-key branch (see api_client.py) and every test hits the
    ephemeral jobagentweb_server with a key that's meaningless there (it's scoped to
    a real user_id on the real production DB), 401 on every request, isolated user
    registration or not."""
    monkeypatch.setattr(api_client, "JOBAGENT_API_KEY", None)


@pytest.fixture(autouse=True)
def _isolated_user(jobagentweb_server, tmp_path, monkeypatch):
    """Every test gets its own freshly-registered JobAgentWeb user, perfect
    isolation with no truncation step needed, since every table is scoped by
    user_id (the shared job_postings/job_embeddings pool is the one exception,
    but tests that touch it use unique-per-test URLs anyway)."""
    monkeypatch.setattr(api_client, "_SESSION_FILE", tmp_path / "session.json")
    username = f"test_{uuid.uuid4().hex[:16]}"
    api_client.register(username, "test-password-not-real", invite_code="test-invite-code")
    yield username


@pytest.fixture
def flask_client():
    """Flask test client with an already-logged-in session (see _isolated_user)."""
    from web.app import app
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client
