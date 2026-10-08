from collector.query_matcher import job_matches_query, primary_query_token, query_matches, query_tokens
from collector.taxonomy import classify_text


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


def test_job_role_must_be_present_in_title():
    assert not job_matches_query(
        "Software Engineer",
        "Customer Success Manager",
        "Works closely with software engineers",
    )


def test_job_technology_can_be_present_in_description():
    assert job_matches_query(
        "PHP Developer",
        "Senior Backend Engineer",
        "Production services written in PHP",
    )


def test_job_rejects_unrelated_engineering_specialty():
    assert not job_matches_query(
        "Python Developer",
        "Senior Security Engineer",
        "Automation and tooling written in Python",
    )


def test_job_rejects_customer_engineering_role():
    assert not job_matches_query(
        "Python Developer",
        "Principal Customer Engineer",
        "Helps customers deploy Python applications",
    )


def test_job_rejects_different_developer_specialty():
    assert not job_matches_query(
        "JavaScript Developer",
        "Salesforce Developer",
        "Uses JavaScript for custom integrations",
    )


def test_framework_alias_discovers_technology_category():
    assert job_matches_query(
        "Python Developer",
        "Senior Backend Engineer",
        "Build APIs with Django and PostgreSQL",
    )


def test_broad_software_role_matches_without_technology_in_title():
    assert job_matches_query("Software Developer", "Senior Software Engineer", "Distributed systems")


def test_broad_software_role_includes_specialized_engineering_roles():
    assert job_matches_query("Software Engineer", "Senior Backend Engineer", "Distributed systems")
    assert job_matches_query("Software Engineer", "Frontend Developer", "Web application")


def test_qa_aliases_share_one_role_family():
    assert query_matches("QA Engineer", "Senior SDET")
    assert query_matches("QA Engineer", "Test Automation Specialist")


def test_taxonomy_classifies_new_technology_categories():
    classification = classify_text("Backend Engineer", "Spring Boot, ASP.NET and Golang")
    assert classification.technologies == {"java", "dotnet", "go"}
    assert classification.role_families == {"backend", "software_engineering"}
