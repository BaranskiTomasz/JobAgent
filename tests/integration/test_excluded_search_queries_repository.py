import pytest

import api_client
from db.repositories import excluded_search_queries_repository as repo


class TestExclude:
    def test_appears_in_get_excluded(self):
        repo.exclude("linkedin", "PHP Developer", "reject rate 97% over 30 jobs")
        assert repo.get_excluded("linkedin") == {"PHP Developer": "reject rate 97% over 30 jobs"}

    def test_reexcluding_updates_reason_instead_of_erroring(self):
        repo.exclude("linkedin", "PHP Developer", "old reason")
        repo.exclude("linkedin", "PHP Developer", "new reason")
        assert repo.get_excluded("linkedin") == {"PHP Developer": "new reason"}
        assert len(repo.get_all()) == 1

    def test_scoped_by_source(self):
        repo.exclude("linkedin", "PHP Developer", "reason")
        assert repo.get_excluded("remotive") == {}


class TestGetAll:
    def test_returns_empty_when_none_excluded(self):
        assert repo.get_all() == []

    def test_includes_id_and_source(self):
        repo.exclude("linkedin", "PHP Developer", "reason")
        rows = repo.get_all()
        assert len(rows) == 1
        assert rows[0]["source"] == "linkedin"
        assert rows[0]["search_query"] == "PHP Developer"
        assert "id" in rows[0]


class TestReinstate:
    def test_removes_from_excluded(self):
        repo.exclude("linkedin", "PHP Developer", "reason")
        id_ = repo.get_all()[0]["id"]
        repo.reinstate(id_)
        assert repo.get_excluded("linkedin") == {}

    def test_reinstating_unknown_id_raises_404(self):
        with pytest.raises(api_client.ApiError) as exc_info:
            repo.reinstate(999)
        assert exc_info.value.status_code == 404
