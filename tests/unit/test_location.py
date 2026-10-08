from collector.location import location_matches, workplace_suffix


class TestWorkplaceSuffix:
    def test_remote_only(self):
        assert workplace_suffix({"remote"}) == " (Remote)"

    def test_hybrid_only(self):
        assert workplace_suffix({"hybrid"}) == " (Hybrid)"

    def test_onsite_only_has_no_suffix(self):
        assert workplace_suffix({"onsite"}) == ""

    def test_empty_set_has_no_suffix(self):
        assert workplace_suffix(set()) == ""

    def test_remote_wins_over_hybrid_when_both_present(self):
        assert workplace_suffix({"remote", "hybrid"}) == " (Remote)"

    def test_hybrid_wins_over_onsite_when_both_present(self):
        assert workplace_suffix({"hybrid", "onsite"}) == " (Hybrid)"


def test_country_code_alias_matches_as_token_only():
    assert location_matches("Remote, PL", "Poland")
    assert not location_matches("Multiple locations", "Poland")


def test_eu_token_matches_supported_country():
    assert location_matches("EU", "Poland")
    assert location_matches("EU only", "Bulgaria")


def test_explicit_country_exclusion_overrides_broad_region():
    assert not location_matches("Worldwide except Poland", "Poland")
    assert not location_matches("Europe excluding Bulgaria", "Bulgaria")
