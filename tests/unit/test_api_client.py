import json
from unittest.mock import MagicMock, patch

import pytest

import api_client


@pytest.fixture(autouse=True)
def _clean_client_state(tmp_path, monkeypatch):
    """The root tests/conftest.py has its own autouse fixture that registers a
    real JobAgentWeb user and saves a real session cookie for every test in the
    whole suite (this project's own "hit a real backend, not mocks"
    convention). These tests exercise api_client's request-construction logic
    itself, the header/branching/error-message decisions, in isolation from
    that, so they start from a deliberately clean slate: no session file, no
    API key, regardless of what already ran. Applied after that fixture (both
    are function-scoped; this one is requested last, in the test module
    itself, so it runs later in setup), never touches the real
    ~/.jobagent/session.json for this machine's actual installation."""
    monkeypatch.setattr(api_client, "_SESSION_FILE", tmp_path / "isolated_session.json")
    monkeypatch.setattr(api_client, "JOBAGENT_API_KEY", None)


def _mock_response(status_code=200, json_body=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body or {}
    return resp


class TestLoggedIn:
    def test_false_when_nothing_configured(self):
        assert api_client.logged_in() is False

    def test_true_when_session_file_exists(self):
        api_client._save_cookie("some-cookie-value")
        assert api_client.logged_in() is True

    def test_true_when_api_key_configured_even_without_session_file(self, monkeypatch):
        monkeypatch.setattr(api_client, "JOBAGENT_API_KEY", "my-secret-key")
        assert api_client.logged_in() is True


class TestRequestWithApiKey:
    def test_sends_api_key_header(self, monkeypatch):
        monkeypatch.setattr(api_client, "JOBAGENT_API_KEY", "my-secret-key")
        with patch.object(api_client._client, "request", return_value=_mock_response()) as mock_request:
            api_client.request("GET", "/api/jobs/stats")
        assert mock_request.call_args.kwargs["headers"]["X-JobAgent-Api-Key"] == "my-secret-key"

    def test_no_session_file_required(self, monkeypatch):
        # Regression: this is the entire point of the API key, no session
        # file needs to exist at all, unlike the cookie-based flow.
        monkeypatch.setattr(api_client, "JOBAGENT_API_KEY", "my-secret-key")
        with patch.object(api_client._client, "request", return_value=_mock_response()):
            api_client.request("GET", "/api/jobs/stats")  # must not raise NotLoggedInError

    def test_401_raises_api_key_specific_message(self, monkeypatch):
        monkeypatch.setattr(api_client, "JOBAGENT_API_KEY", "my-secret-key")
        with patch.object(api_client._client, "request", return_value=_mock_response(status_code=401)):
            with pytest.raises(api_client.NotLoggedInError, match="JOBAGENT_API_KEY"):
                api_client.request("GET", "/api/jobs/stats")

    def test_preserves_caller_supplied_headers(self, monkeypatch):
        monkeypatch.setattr(api_client, "JOBAGENT_API_KEY", "my-secret-key")
        with patch.object(api_client._client, "request", return_value=_mock_response()) as mock_request:
            api_client.request("GET", "/api/jobs/stats", headers={"X-Custom": "1"})
        sent_headers = mock_request.call_args.kwargs["headers"]
        assert sent_headers["X-Custom"] == "1"
        assert sent_headers["X-JobAgent-Api-Key"] == "my-secret-key"


class TestApiKeyFallbackToCookie:
    # Regression: a freshly re-provisioned server whose .env is missing the key
    # used to mean every call 401s forever, even if this installation still has
    # a valid, unexpired session cookie on disk from before the key was set up.

    def test_falls_back_to_cookie_and_succeeds(self, monkeypatch):
        monkeypatch.setattr(api_client, "JOBAGENT_API_KEY", "my-secret-key")
        api_client._save_cookie("saved-cookie-value")
        responses = [_mock_response(status_code=401), _mock_response(status_code=200, json_body={"ok": True})]
        with patch.object(api_client._client, "request", side_effect=responses) as mock_request:
            resp = api_client.request("GET", "/api/jobs/stats")
        assert resp.status_code == 200
        assert mock_request.call_count == 2

    def test_fallback_retry_drops_the_api_key_header(self, monkeypatch):
        monkeypatch.setattr(api_client, "JOBAGENT_API_KEY", "my-secret-key")
        api_client._save_cookie("saved-cookie-value")
        responses = [_mock_response(status_code=401), _mock_response(status_code=200)]
        with patch.object(api_client._client, "request", side_effect=responses) as mock_request:
            api_client.request("GET", "/api/jobs/stats")
        retry_headers = mock_request.call_args_list[1].kwargs["headers"]
        assert "X-JobAgent-Api-Key" not in retry_headers

    def test_fallback_retry_also_401_raises_combined_message(self, monkeypatch):
        monkeypatch.setattr(api_client, "JOBAGENT_API_KEY", "my-secret-key")
        api_client._save_cookie("saved-cookie-value")
        responses = [_mock_response(status_code=401), _mock_response(status_code=401)]
        with patch.object(api_client._client, "request", side_effect=responses):
            with pytest.raises(api_client.NotLoggedInError, match="JOBAGENT_API_KEY") as exc_info:
                api_client.request("GET", "/api/jobs/stats")
        assert "also tried" in str(exc_info.value)

    def test_no_cookie_to_fall_back_to_raises_immediately(self, monkeypatch):
        # No session file at all (the _clean_client_state fixture's default
        # state), same behavior as before this fix, single attempt.
        monkeypatch.setattr(api_client, "JOBAGENT_API_KEY", "my-secret-key")
        with patch.object(api_client._client, "request", return_value=_mock_response(status_code=401)) as mock_request:
            with pytest.raises(api_client.NotLoggedInError, match="JOBAGENT_API_KEY"):
                api_client.request("GET", "/api/jobs/stats")
        assert mock_request.call_count == 1


class TestRequestWithSessionCookie:
    def test_no_cookie_no_key_raises_not_logged_in(self):
        with pytest.raises(api_client.NotLoggedInError):
            api_client.request("GET", "/api/jobs/stats")

    def test_uses_saved_cookie(self):
        api_client._save_cookie("saved-cookie-value")
        with patch.object(api_client._client, "request", return_value=_mock_response()) as mock_request:
            api_client.request("GET", "/api/jobs/stats")
        mock_request.assert_called_once()

    def test_401_raises_generic_session_message(self):
        api_client._save_cookie("saved-cookie-value")
        with patch.object(api_client._client, "request", return_value=_mock_response(status_code=401)):
            with pytest.raises(api_client.NotLoggedInError, match="scripts/login.py"):
                api_client.request("GET", "/api/jobs/stats")


class TestRetryOnTransientFailure:
    def test_retries_transport_error_then_succeeds(self, monkeypatch):
        monkeypatch.setattr(api_client, "JOBAGENT_API_KEY", "my-secret-key")
        monkeypatch.setattr(api_client.time, "sleep", lambda _: None)
        with patch.object(
            api_client._client, "request",
            side_effect=[api_client.httpx.ConnectError("boom"), _mock_response()],
        ) as mock_request:
            api_client.request("GET", "/api/jobs/stats")
        assert mock_request.call_count == 2

    def test_exhausting_transport_error_retries_raises(self, monkeypatch):
        monkeypatch.setattr(api_client, "JOBAGENT_API_KEY", "my-secret-key")
        monkeypatch.setattr(api_client.time, "sleep", lambda _: None)
        with patch.object(
            api_client._client, "request", side_effect=api_client.httpx.ConnectError("boom"),
        ):
            with pytest.raises(api_client.httpx.ConnectError):
                api_client.request("GET", "/api/jobs/stats")

    def test_retries_502_then_succeeds(self, monkeypatch):
        # A deploy/restart on the server briefly returns 502 through the proxy,
        # a real response (not a TransportError), but still worth retrying.
        monkeypatch.setattr(api_client, "JOBAGENT_API_KEY", "my-secret-key")
        monkeypatch.setattr(api_client.time, "sleep", lambda _: None)
        with patch.object(
            api_client._client, "request",
            side_effect=[_mock_response(status_code=502), _mock_response()],
        ) as mock_request:
            api_client.request("GET", "/api/jobs/stats")
        assert mock_request.call_count == 2

    def test_exhausting_502_retries_raises_api_error(self, monkeypatch):
        monkeypatch.setattr(api_client, "JOBAGENT_API_KEY", "my-secret-key")
        monkeypatch.setattr(api_client.time, "sleep", lambda _: None)
        with patch.object(
            api_client._client, "request", return_value=_mock_response(status_code=502),
        ):
            with pytest.raises(api_client.ApiError) as excinfo:
                api_client.request("GET", "/api/jobs/stats")
        assert excinfo.value.status_code == 502

    def test_404_is_not_retried(self, monkeypatch):
        monkeypatch.setattr(api_client, "JOBAGENT_API_KEY", "my-secret-key")
        with patch.object(
            api_client._client, "request", return_value=_mock_response(status_code=404),
        ) as mock_request:
            with pytest.raises(api_client.ApiError):
                api_client.request("GET", "/api/jobs/stats")
        assert mock_request.call_count == 1
