"""Thin HTTP client for JobAgentWeb, the sole data store now. Every
db/repositories/*.py function goes through here instead of raw SQL. Auth is a
single persisted session cookie, shared by every script and the dashboard
alike: this JobAgent installation acts as one already-authenticated identity.
"""
import json
import os
import time
from pathlib import Path

import httpx

from config import JOBAGENTWEB_BASE_URL, JOBAGENT_API_KEY

_SESSION_FILE = Path.home() / ".jobagent" / "session.json"

# Reused across every call instead of opening a fresh TCP/TLS connection per
# request. Never explicitly closed: used both by short-lived scripts (process
# exit cleans up the socket) and the long-running dashboard.
_client = httpx.Client(base_url=JOBAGENTWEB_BASE_URL, timeout=30.0)


class NotLoggedInError(Exception):
    pass


class ApiError(Exception):
    """A request reached the server but it returned an error response."""
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"{status_code}: {detail}")


def _raise_for_status(resp: httpx.Response) -> httpx.Response:
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except json.JSONDecodeError:
            detail = resp.text
        raise ApiError(resp.status_code, detail)
    return resp


def _load_cookie() -> str | None:
    if not _SESSION_FILE.exists():
        return None
    try:
        return json.loads(_SESSION_FILE.read_text()).get("session_cookie")
    except (json.JSONDecodeError, OSError):
        return None


def _save_cookie(cookie_value: str) -> None:
    _SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    _SESSION_FILE.write_text(json.dumps({"session_cookie": cookie_value}))
    # Owner-only, since this file is a live, unattended login. No-op on
    # Windows, but real protection on macOS/Linux.
    os.chmod(_SESSION_FILE, 0o600)


def login(username: str, password: str) -> None:
    resp = httpx.post(
        f"{JOBAGENTWEB_BASE_URL}/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    cookie = resp.cookies.get("session")
    if resp.status_code != 303 or not cookie:
        raise NotLoggedInError("Login failed, check your username and password.")
    _save_cookie(cookie)


def register(username: str, password: str, invite_code: str = "") -> None:
    resp = httpx.post(
        f"{JOBAGENTWEB_BASE_URL}/register",
        data={"username": username, "password": password, "password_confirm": password, "invite_code": invite_code},
        follow_redirects=False,
    )
    cookie = resp.cookies.get("session")
    if resp.status_code != 303 or not cookie:
        raise NotLoggedInError(f"Registration failed: {resp.text}")
    _save_cookie(cookie)


def logged_in() -> bool:
    # A configured API key is this installation's credential directly, no
    # session file needed.
    if JOBAGENT_API_KEY:
        return True
    return _load_cookie() is not None


def request(method: str, path: str, **kwargs):
    # Retries transient network/tunnel blips with backoff, never a 4xx (a real
    # error, not a connectivity hiccup). Prefers the static API key over the
    # session-cookie flow when both could apply: the key never expires and
    # isn't affected by session_epoch logout.
    if JOBAGENT_API_KEY:
        headers = dict(kwargs.pop("headers", None) or {})
        headers["X-JobAgent-Api-Key"] = JOBAGENT_API_KEY
        kwargs["headers"] = headers
    else:
        cookie = _load_cookie()
        if not cookie:
            raise NotLoggedInError(
                "Not logged in. Run `python scripts/login.py`, or register an account "
                f"at {JOBAGENTWEB_BASE_URL}/register first."
            )

        # Cleared and re-set on every call: httpx.Client auto-captures
        # Set-Cookie into its own jar, and that entry can coexist alongside
        # this explicit one instead of replacing it, sending two "session"
        # cookies at once with the server picking whichever it sees first.
        _client.cookies.clear()
        _client.cookies.set("session", cookie)

    last_error = None
    for attempt in range(3):
        try:
            resp = _client.request(method, path, **kwargs)
            break
        except httpx.TransportError as e:
            last_error = e
            time.sleep(0.5 * (attempt + 1))
    else:
        raise last_error

    if resp.status_code == 401:
        if JOBAGENT_API_KEY:
            # The key path failed. Fall back to a stored session cookie once
            # before giving up, in case this installation still has a valid one.
            cookie = _load_cookie()
            if cookie:
                fallback_headers = dict(kwargs.get("headers") or {})
                fallback_headers.pop("X-JobAgent-Api-Key", None)
                kwargs["headers"] = fallback_headers
                _client.cookies.clear()
                _client.cookies.set("session", cookie)
                resp = _client.request(method, path, **kwargs)
                if resp.status_code != 401:
                    return _raise_for_status(resp)
            raise NotLoggedInError(
                "JobAgentWeb rejected JOBAGENT_API_KEY, check it matches JOBAGENT_API_KEY "
                "in JobAgentWeb's own .env on the server."
                + (" (A stored session cookie was also tried as a fallback and rejected too.)" if cookie else "")
            )
        raise NotLoggedInError("Session expired or invalid, run `python scripts/login.py` again.")
    return _raise_for_status(resp)


def get(path: str, **kwargs) -> httpx.Response:
    return request("GET", path, **kwargs)


def post(path: str, **kwargs) -> httpx.Response:
    return request("POST", path, **kwargs)


def patch(path: str, **kwargs) -> httpx.Response:
    return request("PATCH", path, **kwargs)


def put(path: str, **kwargs) -> httpx.Response:
    return request("PUT", path, **kwargs)


def delete(path: str, **kwargs) -> httpx.Response:
    return request("DELETE", path, **kwargs)
