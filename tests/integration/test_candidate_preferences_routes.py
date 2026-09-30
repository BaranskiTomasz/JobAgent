from unittest.mock import MagicMock, patch

from db.repositories import candidate_preferences_repository, criteria_repository


def _mock_claude_response(text: str):
    resp = MagicMock()
    resp.content = [MagicMock(text=text)]
    return resp


class TestGetPreferences:
    def test_returns_empty_dict_when_none_saved(self, flask_client):
        resp = flask_client.get("/api/candidate-preferences")
        assert resp.status_code == 200
        assert resp.json == {}

    def test_returns_active_preferences(self, flask_client):
        candidate_preferences_repository.insert(None, {"open_notes": "async culture please"})
        resp = flask_client.get("/api/candidate-preferences")
        assert resp.status_code == 200
        assert resp.json["open_notes"] == "async culture please"


class TestSavePreferences:
    @patch("web.routes.candidate_preferences.anthropic.Anthropic")
    def test_saves_and_returns_id(self, mock_anthropic, flask_client):
        mock_anthropic.return_value.messages.create.return_value = _mock_claude_response('["PHP", "Symfony Developer"]')
        resp = flask_client.post("/api/candidate-preferences", json={
            "extra_tech": ["PHP", "Symfony"],
            "role_types": ["developer"],
            "seniority_levels": ["senior"],
        })
        assert resp.status_code == 200
        assert resp.json["ok"] is True
        assert "id" in resp.json

    @patch("web.routes.candidate_preferences.anthropic.Anthropic")
    def test_persists_fields_as_active(self, mock_anthropic, flask_client):
        mock_anthropic.return_value.messages.create.return_value = _mock_claude_response('["PHP"]')
        flask_client.post("/api/candidate-preferences", json={"work_mode": ["remote"], "remote_countries": ["Poland"]})
        active = candidate_preferences_repository.get_active()
        assert active["work_mode"] == ["remote"]
        assert active["remote_countries"] == ["Poland"]

    @patch("web.routes.candidate_preferences.anthropic.Anthropic")
    def test_derived_queries_sync_into_title_criteria(self, mock_anthropic, flask_client):
        mock_anthropic.return_value.messages.create.return_value = _mock_claude_response(
            '["PHP", "Symfony Developer"]'
        )
        resp = flask_client.post("/api/candidate-preferences", json={
            "extra_tech": ["PHP", "Symfony"],
        })
        assert resp.status_code == 200
        titles = criteria_repository.get_active("title")
        assert set(titles) == {"PHP", "Symfony Developer"}

    @patch("web.routes.candidate_preferences.anthropic.Anthropic")
    def test_derivation_failure_does_not_fail_the_save(self, mock_anthropic, flask_client):
        mock_anthropic.return_value.messages.create.side_effect = Exception("API down")
        resp = flask_client.post("/api/candidate-preferences", json={"extra_tech": ["PHP"]})
        assert resp.status_code == 200
        assert resp.json["warnings"]

    @patch("web.routes.candidate_preferences.log_anthropic")
    @patch("web.routes.candidate_preferences.anthropic.Anthropic")
    def test_derivation_logs_anthropic_usage(self, mock_anthropic, mock_log, flask_client):
        # Regression: this call site never reported cost, undercounting the
        # usage dashboard for a user-triggered (questionnaire save) action.
        response = _mock_claude_response('["PHP", "Symfony Developer"]')
        mock_anthropic.return_value.messages.create.return_value = response
        flask_client.post("/api/candidate-preferences", json={
            "extra_tech": ["PHP", "Symfony"], "role_types": ["developer"], "seniority_levels": ["senior"],
        })
        mock_log.assert_called_once()
        assert mock_log.call_args.args[0] is response

    @patch("web.routes.candidate_preferences.anthropic.Anthropic")
    def test_remote_countries_sync_into_location_criteria(self, mock_anthropic, flask_client):
        mock_anthropic.return_value.messages.create.return_value = _mock_claude_response("[]")
        flask_client.post("/api/candidate-preferences", json={
            "work_mode": ["remote"],
            "remote_countries": ["Poland", "Germany"],
        })
        locations = criteria_repository.get_active("location")
        assert set(locations) == {"Poland", "Germany"}

    @patch("web.routes.candidate_preferences.anthropic.Anthropic")
    def test_work_country_drives_remote_eligibility_location(self, mock_anthropic, flask_client):
        mock_anthropic.return_value.messages.create.return_value = _mock_claude_response("[]")
        flask_client.post("/api/candidate-preferences", json={
            "work_mode": ["remote"],
            "work_country": "Poland",
            "employer_countries": ["United States", "United Kingdom"],
        })
        assert criteria_repository.get_active("location") == ["Poland"]

    @patch("web.routes.candidate_preferences.anthropic.Anthropic")
    def test_onsite_work_mode_uses_cities_not_countries(self, mock_anthropic, flask_client):
        mock_anthropic.return_value.messages.create.return_value = _mock_claude_response("[]")
        flask_client.post("/api/candidate-preferences", json={
            "work_mode": ["onsite"],
            "remote_countries": ["Poland"],
            "hybrid_cities": ["Warsaw"],
        })
        locations = criteria_repository.get_active("location")
        assert locations == ["Warsaw"]

    @patch("web.routes.candidate_preferences.anthropic.Anthropic")
    def test_hybrid_work_mode_uses_cities(self, mock_anthropic, flask_client):
        mock_anthropic.return_value.messages.create.return_value = _mock_claude_response("[]")
        flask_client.post("/api/candidate-preferences", json={
            "work_mode": ["hybrid"],
            "hybrid_cities": ["Kraków"],
        })
        assert criteria_repository.get_active("location") == ["Kraków"]

    @patch("web.routes.candidate_preferences.anthropic.Anthropic")
    def test_remote_and_hybrid_combine_countries_and_cities(self, mock_anthropic, flask_client):
        mock_anthropic.return_value.messages.create.return_value = _mock_claude_response("[]")
        flask_client.post("/api/candidate-preferences", json={
            "work_mode": ["remote", "hybrid"],
            "remote_countries": ["Germany"],
            "hybrid_cities": ["Warsaw"],
        })
        locations = criteria_repository.get_active("location")
        assert set(locations) == {"Germany", "Warsaw"}

    @patch("web.routes.candidate_preferences.anthropic.Anthropic")
    def test_switching_away_from_remote_clears_stale_location_criteria(self, mock_anthropic, flask_client):
        mock_anthropic.return_value.messages.create.return_value = _mock_claude_response("[]")
        flask_client.post("/api/candidate-preferences", json={
            "work_mode": ["remote"],
            "remote_countries": ["Poland", "Germany"],
        })
        flask_client.post("/api/candidate-preferences", json={
            "work_mode": ["onsite"],
            "remote_countries": ["Poland", "Germany"],
            "hybrid_cities": ["Warsaw"],
        })
        locations = criteria_repository.get_active("location")
        assert locations == ["Warsaw"]

    @patch("web.routes.candidate_preferences.anthropic.Anthropic")
    def test_no_work_mode_clears_location_criteria(self, mock_anthropic, flask_client):
        mock_anthropic.return_value.messages.create.return_value = _mock_claude_response("[]")
        flask_client.post("/api/candidate-preferences", json={
            "work_mode": ["remote"],
            "remote_countries": ["Poland"],
        })
        flask_client.post("/api/candidate-preferences", json={"work_mode": []})
        assert criteria_repository.get_active("location") == []

    @patch("web.routes.candidate_preferences.anthropic.Anthropic")
    def test_resaving_replaces_previous_derived_titles(self, mock_anthropic, flask_client):
        mock_anthropic.return_value.messages.create.return_value = _mock_claude_response('["PHP"]')
        flask_client.post("/api/candidate-preferences", json={"extra_tech": ["PHP"]})

        mock_anthropic.return_value.messages.create.return_value = _mock_claude_response('["Python"]')
        flask_client.post("/api/candidate-preferences", json={"extra_tech": ["Python"]})

        titles = criteria_repository.get_active("title")
        assert titles == ["Python"]

    def test_invalid_field_returns_400(self, flask_client):
        resp = flask_client.post("/api/candidate-preferences", json={"not_a_real_field": 1})
        assert resp.status_code == 400

    @patch("web.routes.candidate_preferences.anthropic.Anthropic")
    def test_avoided_tech_syncs_into_rejected_criteria(self, mock_anthropic, flask_client):
        mock_anthropic.return_value.messages.create.return_value = _mock_claude_response("[]")
        flask_client.post("/api/candidate-preferences", json={"avoided_tech": ["WordPress", "jQuery"]})
        rejected = criteria_repository.get_active("rejected")
        assert set(rejected) == {"WordPress", "jQuery"}

    @patch("web.routes.candidate_preferences.anthropic.Anthropic")
    def test_clearing_avoided_tech_clears_rejected_criteria(self, mock_anthropic, flask_client):
        mock_anthropic.return_value.messages.create.return_value = _mock_claude_response("[]")
        flask_client.post("/api/candidate-preferences", json={"avoided_tech": ["WordPress"]})
        flask_client.post("/api/candidate-preferences", json={"avoided_tech": []})
        assert criteria_repository.get_active("rejected") == []

    @patch("web.routes.candidate_preferences.anthropic.Anthropic")
    def test_extra_tech_syncs_into_preferred_criteria(self, mock_anthropic, flask_client):
        # Regression: the scorer prompt's PREFERRED section was only ever populated
        # by the separate CV-derived apply-criteria flow, which nothing in this UI
        # calls, extra_tech was collected (05 · Technologies) but never reached it.
        mock_anthropic.return_value.messages.create.return_value = _mock_claude_response("[]")
        flask_client.post("/api/candidate-preferences", json={"extra_tech": ["Rust", "Kubernetes"]})
        preferred = criteria_repository.get_active("preferred")
        assert set(preferred) == {"Rust", "Kubernetes"}

    @patch("web.routes.candidate_preferences.anthropic.Anthropic")
    def test_clearing_extra_tech_clears_preferred_criteria(self, mock_anthropic, flask_client):
        mock_anthropic.return_value.messages.create.return_value = _mock_claude_response("[]")
        flask_client.post("/api/candidate-preferences", json={"extra_tech": ["Rust"]})
        flask_client.post("/api/candidate-preferences", json={"extra_tech": []})
        assert criteria_repository.get_active("preferred") == []

    @patch("web.routes.candidate_preferences.anthropic.Anthropic")
    def test_extra_tech_does_not_populate_required_criteria(self, mock_anthropic, flask_client):
        # Deliberate: populating "required" from the questionnaire, combined with
        # the keyword pre-filter's now-functional \b-boundary regex, could reject
        # an entire pool for a candidate whose stack includes e.g. C#/C++/.NET.
        mock_anthropic.return_value.messages.create.return_value = _mock_claude_response("[]")
        flask_client.post("/api/candidate-preferences", json={"extra_tech": ["Rust"]})
        assert criteria_repository.get_active("required") == []
