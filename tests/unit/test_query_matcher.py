from collector.query_matcher import primary_query_token, query_matches, query_tokens


def test_role_aliases_match():
    assert query_matches("Backend Developer", "Senior Backend Software Engineer")


def test_seniority_does_not_narrow_discovery():
    assert query_matches("Senior PHP Developer", "PHP Engineer")


def test_framework_can_match_skills_or_description():
    assert query_matches("Symfony Developer", "Backend Engineer", "PHP Symfony API platform")


def test_all_meaningful_tokens_are_required():
    assert not query_matches("Python Data Engineer", "Python Backend Engineer")


def test_technology_spelling_is_normalized():
    assert query_tokens(".NET Node.js") == ("net", "node")


def test_primary_token_avoids_seniority_and_generic_role():
    assert primary_query_token("Senior Backend Engineer") == "backend"
