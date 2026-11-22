import pytest

from jobhunt.enums import Ats
from jobhunt.filters import contractor_ok, keep_job, requires_other_language, visa_sponsorship
from jobhunt.location import classify
from jobhunt.models import Board, Job


def job(location: str, title: str = "Backend Engineer", description: str = "") -> Job:
    return Job(
        id="greenhouse:acme:1",
        board=Board(company="Acme", ats=Ats.greenhouse, slug="acme"),
        external_id="1",
        title=title,
        url="https://x",
        apply_url="https://x/apply",
        offices=[classify(p) for p in location.split(";")],
        description=description,
        posted_at=None,
    )


@pytest.mark.parametrize(
    "text",
    [
        "Fluent German is required.",
        "You are fluent in English and German.",
        "Business-level French (C1) is a must",
        "German (C1/C2)",
        "Native-level Spanish speaker",
        "Spanish-speaking market",
        "Proficiency in Dutch",
        "Excellent written and spoken Polish",
        "Senior Sales Engineer, German Speaker",
        "Deutschkenntnisse",
        "Sie sprechen fließend Deutsch",
    ],
)
def test_detects_other_language_requirement(text: str) -> None:
    assert requires_other_language(text)


@pytest.mark.parametrize(
    "text",
    [
        "Fluent English is required.",
        "You will join our German office in Berlin.",
        "Experience with Python, Go and PostgreSQL.",
        "Our team spans Germany, France and Spain.",
        "",
    ],
)
def test_english_only_or_no_requirement(text: str) -> None:
    assert not requires_other_language(text)


def test_keep_job_remote_country_without_language_requirement() -> None:
    assert keep_job(job("Remote Germany", description="English is our working language."))


def test_keep_job_remote_country_requiring_german_is_dropped() -> None:
    assert not keep_job(job("Remote Germany", description="Fluent German is required."))


def test_keep_job_language_rule_also_reads_title() -> None:
    assert not keep_job(job("Remote, Spain", title="Account Executive, Spanish Speaker"))


def test_language_rule_only_applies_to_remote_europe_country() -> None:
    assert keep_job(job("Bucharest, Romania", description="Fluent German is required."))
    assert keep_job(job("Remote - Europe", description="Fluent German is required."))


def test_romania_office_wins_over_remote_country_language_rule() -> None:
    assert keep_job(job("Remote Germany;Bucharest", description="Fluent German is required."))


def test_other_only_is_dropped() -> None:
    assert not keep_job(job("New York, NY"))
    assert not keep_job(job("Remote - Toronto"))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Visa sponsorship is available for this role.", True),
        ("We can sponsor visas for exceptional candidates.", True),
        ("We offer relocation and visa support.", True),
        ("We are unable to sponsor visas at this time.", False),
        ("This role is not eligible for visa sponsorship.", False),
        ("Applicants must be authorized to work in the US without sponsorship.", False),
        ("You must have the right to work in the UK.", False),
        ("Experience with Python and Kubernetes.", None),
    ],
)
def test_visa_sponsorship_signal(text: str, expected: bool | None) -> None:
    assert visa_sponsorship(text) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("This is a contractor position.", True),
        ("Open to independent contractors worldwide.", True),
        ("B2B contract possible.", True),
        ("Freelance engagement, 6 months.", True),
        ("We hire globally through an Employer of Record.", True),
        ("Full-time employee position with benefits.", False),
        ("You will contract with our partners on behalf of customers.", False),
    ],
)
def test_contractor_signal(text: str, expected: bool) -> None:
    assert contractor_ok(text) is expected


def test_us_remote_kept_with_sponsorship() -> None:
    assert keep_job(job("Remote, United States", description="Visa sponsorship is available."))


def test_uk_remote_kept_for_contractors() -> None:
    assert keep_job(job("Remote - London, UK", description="Open to B2B contractors."))


def test_us_remote_kept_when_hiring_outside_the_country() -> None:
    assert keep_job(job("Remote - US", description="We also hire candidates outside the US."))


def test_us_remote_silent_is_dropped() -> None:
    assert not keep_job(job("Remote, United States", description="Python, Go, Postgres."))


def test_us_remote_no_sponsorship_is_dropped() -> None:
    assert not keep_job(job("Remote - US", description="We are unable to sponsor visas."))


def test_contractor_beats_no_sponsorship() -> None:
    text = "We do not sponsor visas. Contractors welcome."
    assert keep_job(job("Remote - US", description=text))


def test_us_remote_language_rule_still_applies() -> None:
    text = "Visa sponsorship available. Fluent Spanish required."
    assert not keep_job(job("Remote - US", description=text))


def test_eu_country_needs_no_visa_signal() -> None:
    assert keep_job(job("Remote Germany", description="Python, Go."))


@pytest.mark.parametrize(
    "text",
    [
        "See https://cdn.openai.com/policies/global-employee-and-contractor-privacy-policy.pdf",
        "Our employee and contractor privacy policy applies.",
        "drawing on your trusted bench of freelancers, agencies, and production partners",
        "We partner with government contractors.",
    ],
)
def test_boilerplate_is_not_a_contractor_signal(text: str) -> None:
    assert not contractor_ok(text)


@pytest.mark.parametrize(
    "text",
    [
        "Contractors welcome.",
        "You can join as an independent contractor.",
        "This is a 6-month contract.",
    ],
)
def test_role_level_contractor_wording(text: str) -> None:
    assert contractor_ok(text)


def test_anthropic_style_sponsorship_is_yes() -> None:
    text = (
        "Visa sponsorship: We do sponsor visas! However, we aren't able to successfully "
        "sponsor visas for every role and every candidate."
    )
    assert visa_sponsorship(text) is True


@pytest.mark.parametrize(
    "text", ["We are not able to sponsor visas.", "We aren't able to sponsor work visas."]
)
def test_not_able_to_sponsor_is_no(text: str) -> None:
    assert visa_sponsorship(text) is False


@pytest.mark.parametrize(
    "text",
    [
        "We will sponsor you for relocation if you need it.",
        "Clearance is not required at hire, and OpenAI will sponsor the clearance process.",
    ],
)
def test_sponsoring_something_else_is_not_visa(text: str) -> None:
    assert visa_sponsorship(text) is None


def test_anywhere_in_the_us_is_not_hiring_abroad() -> None:
    text = "This is a remote role open to candidates anywhere in the United States."
    assert not keep_job(job("Remote - US", description=text))


@pytest.mark.parametrize(
    "text",
    [
        "Lead annual 1099 reporting and support other accounts payable work.",
        "This is a contract to hire full-time on-site role.",
    ],
)
def test_not_a_b2b_contractor_role(text: str) -> None:
    assert not contractor_ok(text)
