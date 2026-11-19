import io
from unittest.mock import MagicMock, patch

from db.repositories import cv_repository, criteria_repository


def _mock_claude_response(text: str):
    resp = MagicMock()
    resp.content = [MagicMock(text=text)]
    return resp


def _upload(flask_client, filename="cv.pdf", extracted_text="Experienced backend engineer."):
    with patch("web.routes.cv._extract_text", return_value=extracted_text), \
         patch("web.routes.cv.anthropic.Anthropic") as mock_anthropic:
        mock_anthropic.return_value.messages.create.return_value = _mock_claude_response(
            '{"stack": ["Python"], "years_experience": 5, "seniority": "Senior", '
            '"location": "Warsaw, Poland", "remote_preference": "fully remote", '
            '"raw_summary": "Experienced backend engineer."}'
        )
        data = {"file": (io.BytesIO(b"%PDF-1.4 fake"), filename)}
        return flask_client.post("/api/cv/upload", data=data, content_type="multipart/form-data")


class TestListAndActive:
    def test_list_empty_by_default(self, flask_client):
        assert flask_client.get("/api/cv").json == []

    def test_active_is_null_when_none_uploaded(self, flask_client):
        assert flask_client.get("/api/cv/active").json is None


class TestUpload:
    def test_no_file_returns_400(self, flask_client):
        resp = flask_client.post("/api/cv/upload", data={}, content_type="multipart/form-data")
        assert resp.status_code == 400
        assert "No file" in resp.json["error"]

    def test_non_pdf_extension_returns_400(self, flask_client):
        data = {"file": (io.BytesIO(b"not a pdf"), "cv.docx")}
        resp = flask_client.post("/api/cv/upload", data=data, content_type="multipart/form-data")
        assert resp.status_code == 400
        assert "PDF" in resp.json["error"]

    def test_unreadable_pdf_returns_400(self, flask_client):
        with patch("web.routes.cv._extract_text", side_effect=Exception("corrupt PDF structure")):
            data = {"file": (io.BytesIO(b"%PDF-1.4 garbage"), "cv.pdf")}
            resp = flask_client.post("/api/cv/upload", data=data, content_type="multipart/form-data")
        assert resp.status_code == 400
        assert "Failed to read PDF" in resp.json["error"]

    def test_empty_extracted_text_returns_400(self, flask_client):
        with patch("web.routes.cv._extract_text", return_value="   "):
            data = {"file": (io.BytesIO(b"%PDF-1.4"), "cv.pdf")}
            resp = flask_client.post("/api/cv/upload", data=data, content_type="multipart/form-data")
        assert resp.status_code == 400
        assert "scanned image" in resp.json["error"]

    def test_claude_parse_failure_returns_500(self, flask_client):
        with patch("web.routes.cv._extract_text", return_value="Some CV text"), \
             patch("web.routes.cv.anthropic.Anthropic") as mock_anthropic:
            mock_anthropic.return_value.messages.create.side_effect = Exception("API down")
            data = {"file": (io.BytesIO(b"%PDF-1.4"), "cv.pdf")}
            resp = flask_client.post("/api/cv/upload", data=data, content_type="multipart/form-data")
        assert resp.status_code == 500
        assert "Failed to parse CV" in resp.json["error"]

    def test_successful_upload_persists_and_activates_profile(self, flask_client):
        resp = _upload(flask_client)
        assert resp.status_code == 200
        assert resp.json["ok"] is True
        assert resp.json["parsed"]["seniority"] == "Senior"

        active = flask_client.get("/api/cv/active").json
        assert active["id"] == resp.json["id"]
        assert active["parsed"]["stack"] == ["Python"]

    def test_upload_logs_anthropic_usage(self, flask_client):
        # Regression: _parse_with_claude never reported cost, undercounting
        # the usage dashboard for this user-triggered action.
        with patch("web.routes.cv.log_anthropic") as mock_log:
            resp = _upload(flask_client)
        assert resp.status_code == 200
        mock_log.assert_called_once()
        assert mock_log.call_args.args[1] == "cv_parse"

    def test_upload_sends_full_extracted_text_to_claude(self, flask_client):
        tail_marker = "UNIQUE_TAIL_MARKER_CERTYFIKAT_AWS"
        extracted_text = "A" * 5000 + tail_marker
        with patch("web.routes.cv._extract_text", return_value=extracted_text), \
             patch("web.routes.cv.anthropic.Anthropic") as mock_anthropic:
            mock_anthropic.return_value.messages.create.return_value = _mock_claude_response(
                '{"stack": [], "years_experience": null, "seniority": "Senior", '
                '"location": "", "remote_preference": "flexible", "raw_summary": ""}'
            )
            data = {"file": (io.BytesIO(b"%PDF-1.4 fake"), "cv.pdf")}
            resp = flask_client.post("/api/cv/upload", data=data, content_type="multipart/form-data")

        assert resp.status_code == 200
        messages = mock_anthropic.return_value.messages.create.call_args.kwargs["messages"]
        assert tail_marker in messages[0]["content"]

    def test_upload_caps_extremely_long_extracted_text(self, flask_client):
        tail_marker = "UNIQUE_TAIL_MARKER_POZA_LIMITEM_60K"
        extracted_text = "B" * 70_000 + tail_marker
        with patch("web.routes.cv._extract_text", return_value=extracted_text), \
             patch("web.routes.cv.anthropic.Anthropic") as mock_anthropic:
            mock_anthropic.return_value.messages.create.return_value = _mock_claude_response(
                '{"stack": [], "years_experience": null, "seniority": "Senior", '
                '"location": "", "remote_preference": "flexible", "raw_summary": ""}'
            )
            data = {"file": (io.BytesIO(b"%PDF-1.4 fake"), "cv.pdf")}
            resp = flask_client.post("/api/cv/upload", data=data, content_type="multipart/form-data")

        assert resp.status_code == 200
        messages = mock_anthropic.return_value.messages.create.call_args.kwargs["messages"]
        assert tail_marker not in messages[0]["content"]


class TestActivate:
    def test_activate_switches_active_profile(self, flask_client):
        first = _upload(flask_client, filename="a.pdf").json["id"]
        second = _upload(flask_client, filename="b.pdf").json["id"]
        assert flask_client.get("/api/cv/active").json["id"] == second

        resp = flask_client.post(f"/api/cv/{first}/activate")
        assert resp.status_code == 200
        assert flask_client.get("/api/cv/active").json["id"] == first

    def test_activate_nonexistent_profile_404s_cleanly(self, flask_client):
        # Regression: JobAgentWeb's set_active now 404s on a bad id; without
        # the try/except here, that ApiError propagated uncaught out of the
        # Flask route as an unhandled 500 instead of a clean error response.
        resp = flask_client.post("/api/cv/999999/activate")
        assert resp.status_code == 404


class TestSuggestCriteria:
    def _suggest_response(self):
        return _mock_claude_response(
            '{"search_queries": ["PHP developer"], "titles": ["Senior PHP Developer"], '
            '"locations": ["Remote"], "required": ["PHP"], "preferred": ["Symfony"]}'
        )

    def test_suggest_for_active_profile(self, flask_client):
        _upload(flask_client)
        with patch("web.routes.cv.anthropic.Anthropic") as mock_anthropic:
            mock_anthropic.return_value.messages.create.return_value = self._suggest_response()
            resp = flask_client.post("/api/cv/0/suggest-criteria")
        assert resp.status_code == 200
        assert resp.json["required"] == ["PHP"]

    def test_suggest_for_nonexistent_profile_404s(self, flask_client):
        resp = flask_client.post("/api/cv/999999/suggest-criteria")
        assert resp.status_code == 404

    def test_suggest_claude_failure_returns_500(self, flask_client):
        _upload(flask_client)
        with patch("web.routes.cv.anthropic.Anthropic") as mock_anthropic:
            mock_anthropic.return_value.messages.create.side_effect = Exception("API down")
            resp = flask_client.post("/api/cv/0/suggest-criteria")
        assert resp.status_code == 500

    def test_suggest_logs_anthropic_usage(self, flask_client):
        _upload(flask_client)
        with patch("web.routes.cv.anthropic.Anthropic") as mock_anthropic, \
             patch("web.routes.cv.log_anthropic") as mock_log:
            mock_anthropic.return_value.messages.create.return_value = self._suggest_response()
            flask_client.post("/api/cv/0/suggest-criteria")
        mock_log.assert_called_once()
        assert mock_log.call_args.args[1] == "cv_suggest_criteria"


class TestApplyCriteria:
    def test_no_criteria_provided_returns_400(self, flask_client):
        resp = flask_client.post("/api/cv/0/apply-criteria", json={})
        assert resp.status_code == 400

    def test_applies_all_provided_types(self, flask_client):
        resp = flask_client.post("/api/cv/0/apply-criteria", json={
            "search_queries": ["PHP developer"],
            "titles": ["Senior PHP Developer"],
            "locations": ["Remote"],
            "required": ["PHP"],
            "preferred": ["Symfony"],
        })
        assert resp.status_code == 200
        assert resp.json["added_required"] == 1
        assert resp.json["added_preferred"] == 1

        active = criteria_repository.get_active_dict()
        assert active["required"] == ["PHP"]
        assert active["preferred"] == ["Symfony"]
