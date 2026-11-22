"""Specs for jobhunt.filters: which crawled jobs are worth storing at all."""

import pytest

from jobhunt.enums import Ats
from jobhunt.filters import keep_job, requires_other_language
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
        "You will join our German office in Berlin.",  # a place, not a skill
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
    """Jobs already open to Romania keep the current behaviour (the rule was asked for these)."""
    assert keep_job(job("Bucharest, Romania", description="Fluent German is required."))
    assert keep_job(job("Remote - Europe", description="Fluent German is required."))


def test_romania_office_wins_over_remote_country_language_rule() -> None:
    assert keep_job(job("Remote Germany;Bucharest", description="Fluent German is required."))


def test_other_only_is_dropped() -> None:
    assert not keep_job(job("New York, NY"))
    assert not keep_job(job("Remote - London, UK"))
