"""Specs for the Greenhouse, Lever and Ashby adapters (src/jobhunt/ats/).

Fixtures are real public board responses (GitLab on Greenhouse, Palantir on Lever,
Linear on Ashby) trimmed to 3 jobs, recorded 2026-11-22.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from jobhunt.ats.base import ADAPTERS, detect_board
from jobhunt.enums import Ats, LocationClass
from jobhunt.http import PoliteClient
from jobhunt.models import Board

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def fast_client() -> PoliteClient:
    return PoliteClient(min_interval=0)


@respx.mock
async def test_greenhouse_parses_fixture() -> None:
    respx.get("https://boards-api.greenhouse.io/v1/boards/gitlab/jobs").mock(
        return_value=httpx.Response(200, json=fixture("greenhouse_jobs.json"))
    )
    board = Board(company="GitLab", ats=Ats.greenhouse, slug="gitlab")
    async with fast_client() as c:
        jobs = await ADAPTERS[Ats.greenhouse].fetch_jobs(c, board)

    assert len(jobs) == 3
    job = jobs[0]
    assert job.id == "greenhouse:gitlab:8704363002"
    assert job.external_id == "8704363002"
    assert job.board == board
    assert job.title == "Backend Engineer (Python),AI Engineering: Agent Foundations"
    assert job.url == "https://job-boards.greenhouse.io/gitlab/jobs/8704363002"
    assert job.apply_url == (
        "https://job-boards.greenhouse.io/embed/job_app?for=gitlab&token=8704363002"
    )
    # location.name holds several places separated by ";" — each becomes its own office,
    # then offices[].location (or .name) are added; exact duplicates are dropped.
    assert [o.raw for o in job.offices] == [
        "Remote, Canada",
        "Remote, Poland",
        "Remote, United Kingdom",
        "Poland",
    ]
    assert job.offices[0].location_class == LocationClass.other
    assert job.posted_at == datetime.fromisoformat("2026-08-19T09:38:27-04:00")
    # Greenhouse `content` is HTML that is itself entity-escaped (&lt;p&gt;...).
    assert job.description.startswith("GitLab is the intelligent orchestration platform")
    assert "<" not in job.description and "&lt;" not in job.description


@respx.mock
async def test_lever_parses_fixture() -> None:
    respx.get("https://api.lever.co/v0/postings/palantir").mock(
        return_value=httpx.Response(200, json=fixture("lever_jobs.json"))
    )
    board = Board(company="Palantir", ats=Ats.lever, slug="palantir")
    async with fast_client() as c:
        jobs = await ADAPTERS[Ats.lever].fetch_jobs(c, board)

    assert len(jobs) == 3
    job = jobs[0]
    assert job.id == "lever:palantir:0ccbe620-a3ef-41d1-a5c4-68e56b3c91d0"
    assert job.title == "American Tech Fellowship"
    assert job.url == "https://jobs.lever.co/palantir/0ccbe620-a3ef-41d1-a5c4-68e56b3c91d0"
    assert job.apply_url == job.url + "/apply"
    # workplaceType == "remote" prefixes "Remote - "; location + allLocations deduped.
    assert [o.raw for o in job.offices] == ["Remote - North America"]
    assert job.offices[0].location_class == LocationClass.other
    assert job.posted_at == datetime.fromtimestamp(1750119374.985, UTC)  # createdAt is ms
    # Not remote: no prefix.
    assert [o.raw for o in jobs[2].offices] == ["London, United Kingdom"]


@respx.mock
async def test_ashby_parses_fixture() -> None:
    respx.get("https://api.ashbyhq.com/posting-api/job-board/linear").mock(
        return_value=httpx.Response(200, json=fixture("ashby_jobs.json"))
    )
    board = Board(company="Linear", ats=Ats.ashby, slug="linear")
    async with fast_client() as c:
        jobs = await ADAPTERS[Ats.ashby].fetch_jobs(c, board)

    assert len(jobs) == 3
    job = jobs[0]
    assert job.id == "ashby:linear:d3bc1ced-3ce4-4086-a050-555055dbb1ff"
    assert job.title == "Senior / Staff Fullstack Engineer"
    assert job.url == "https://jobs.ashbyhq.com/linear/d3bc1ced-3ce4-4086-a050-555055dbb1ff"
    assert job.apply_url == job.url + "/application"
    assert [o.raw for o in job.offices] == ["Remote - Europe"]
    assert job.offices[0].location_class == LocationClass.remote_europe
    assert job.posted_at == datetime.fromisoformat("2021-04-27T20:13:45.158+00:00")
    # Second job is remote North America: offices must come from each job's own data.
    assert [o.raw for o in jobs[1].offices] == ["Remote - North America"]
    assert jobs[1].offices[0].location_class == LocationClass.other


def ashby_job(**overrides: Any) -> dict[str, Any]:
    """A minimal Ashby posting; override fields per test."""
    job = fixture("ashby_jobs.json")["jobs"][0]
    job.update(overrides)
    return job


@respx.mock
async def test_ashby_location_variants() -> None:
    respx.get("https://api.ashbyhq.com/posting-api/job-board/acme").mock(
        return_value=httpx.Response(
            200,
            json={
                "jobs": [
                    # On-site: place kept as-is, no prefix.
                    ashby_job(id="a", isRemote=False, location="Bucharest, Romania"),
                    # Already says remote: no "Remote - Remote".
                    ashby_job(id="b", isRemote=True, location="Remote"),
                    # Secondary locations become extra offices, prefixed too.
                    ashby_job(
                        id="c",
                        isRemote=True,
                        location="Romania",
                        secondaryLocations=[{"location": "Germany"}],
                    ),
                ]
            },
        )
    )
    board = Board(company="Acme", ats=Ats.ashby, slug="acme")
    async with fast_client() as c:
        jobs = await ADAPTERS[Ats.ashby].fetch_jobs(c, board)

    assert [o.raw for o in jobs[0].offices] == ["Bucharest, Romania"]
    assert jobs[0].offices[0].location_class == LocationClass.romania
    assert [o.raw for o in jobs[1].offices] == ["Remote"]
    assert [o.raw for o in jobs[2].offices] == ["Remote - Romania", "Remote - Germany"]


@respx.mock
async def test_lever_eu_host_used() -> None:
    route = respx.get("https://api.eu.lever.co/v0/postings/acme").mock(
        return_value=httpx.Response(200, json=[])
    )
    board = Board(company="Acme", ats=Ats.lever, slug="acme", host="api.eu.lever.co")
    async with fast_client() as c:
        assert await ADAPTERS[Ats.lever].fetch_jobs(c, board) == []
    assert route.called
    assert route.calls[0].request.url.params["mode"] == "json"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://boards.greenhouse.io/acme",
            Board(company="Acme", ats=Ats.greenhouse, slug="acme"),
        ),
        (
            "https://job-boards.greenhouse.io/acme/jobs/123",
            Board(company="Acme", ats=Ats.greenhouse, slug="acme"),
        ),
        (
            "https://boards-api.greenhouse.io/v1/boards/acme/jobs",
            Board(company="Acme", ats=Ats.greenhouse, slug="acme"),
        ),
        (
            "https://jobs.lever.co/acme/abc-123",
            Board(company="Acme", ats=Ats.lever, slug="acme"),
        ),
        (
            "https://jobs.eu.lever.co/acme",
            Board(company="Acme", ats=Ats.lever, slug="acme", host="api.eu.lever.co"),
        ),
        (
            "https://jobs.ashbyhq.com/acme?utm_source=x",
            Board(company="Acme", ats=Ats.ashby, slug="acme"),
        ),
        ("https://example.com/careers", None),
        (
            "https://boards.greenhouse.io/embed/job_board?for=acme",  # embed form: slug in ?for=
            Board(company="Acme", ats=Ats.greenhouse, slug="acme"),
        ),
        # Greenhouse EU data residency (found by probing 2026-11-22; plan missed it).
        (
            "https://job-boards.eu.greenhouse.io/acme",
            Board(company="Acme", ats=Ats.greenhouse, slug="acme"),
        ),
        (
            "https://boards.eu.greenhouse.io/acme",
            Board(company="Acme", ats=Ats.greenhouse, slug="acme"),
        ),
        ("https://boards.greenhouse.io/embed/job_board", None),  # embed without ?for=
        # The vendor's own non-board sites (footer "Powered by …", help centre) and
        # lookalikes are NOT boards: detection is an exact-host allowlist, not a word match.
        ("https://www.greenhouse.io/careers", None),
        ("https://support.greenhouse.io/hc/en-us", None),
        ("https://www.lever.co/", None),
        ("https://greenhouse-plants.com/shop", None),
        ("https://jobs.lever.co/", None),  # no slug: must not crash
    ],
)
def test_detect_board_urls(url: str, expected: Board | None) -> None:
    assert detect_board(url, "Acme") == expected


@respx.mock
@pytest.mark.parametrize(
    ("ats", "url"),
    [
        (Ats.greenhouse, "https://boards-api.greenhouse.io/v1/boards/nope/jobs"),
        (Ats.lever, "https://api.lever.co/v0/postings/nope"),
        (Ats.ashby, "https://api.ashbyhq.com/posting-api/job-board/nope"),
    ],
)
async def test_probe_404_returns_none(ats: Ats, url: str) -> None:
    respx.get(url__startswith=url).mock(return_value=httpx.Response(404))
    async with fast_client() as c:
        assert await ADAPTERS[ats].probe(c, "nope") is None


@respx.mock
async def test_probe_hit_returns_board() -> None:
    respx.get(url__startswith="https://api.ashbyhq.com/posting-api/job-board/linear").mock(
        return_value=httpx.Response(200, json={"jobs": []})
    )
    async with fast_client() as c:
        board = await ADAPTERS[Ats.ashby].probe(c, "linear")
    assert board == Board(company="linear", ats=Ats.ashby, slug="linear")


# --- Final review: one odd posting must not break its board -------------------------------


@respx.mock
async def test_explicit_nulls_treated_as_empty() -> None:
    """Vendors send `null` for empty lists/objects; `.get(key, [])` does not cover that."""
    lever = fixture("lever_jobs.json")
    lever[0]["categories"]["allLocations"] = None
    respx.get("https://api.lever.co/v0/postings/palantir").mock(
        return_value=httpx.Response(200, json=lever)
    )
    gh = fixture("greenhouse_jobs.json")
    gh["jobs"][0]["location"] = None
    gh["jobs"][0]["offices"] = None
    respx.get("https://boards-api.greenhouse.io/v1/boards/gitlab/jobs").mock(
        return_value=httpx.Response(200, json=gh)
    )
    async with fast_client() as c:
        lever_jobs = await ADAPTERS[Ats.lever].fetch_jobs(
            c, Board(company="Palantir", ats=Ats.lever, slug="palantir")
        )
        gh_jobs = await ADAPTERS[Ats.greenhouse].fetch_jobs(
            c, Board(company="GitLab", ats=Ats.greenhouse, slug="gitlab")
        )
    assert [o.raw for o in lever_jobs[0].offices] == ["Remote - North America"]
    assert len(gh_jobs) == 3 and gh_jobs[0].offices == []


@respx.mock
async def test_ashby_null_secondary_locations() -> None:
    respx.get("https://api.ashbyhq.com/posting-api/job-board/acme").mock(
        return_value=httpx.Response(200, json={"jobs": [ashby_job(secondaryLocations=None)]})
    )
    async with fast_client() as c:
        jobs = await ADAPTERS[Ats.ashby].fetch_jobs(
            c, Board(company="Acme", ats=Ats.ashby, slug="acme")
        )
    assert [o.raw for o in jobs[0].offices] == ["Remote - Europe"]


@pytest.mark.parametrize(
    ("ats", "url", "payload", "drop"),
    [
        (
            Ats.greenhouse,
            "https://boards-api.greenhouse.io/v1/boards/b/jobs",
            lambda: fixture("greenhouse_jobs.json"),
            lambda d: d["jobs"][1].pop("title"),
        ),
        (
            Ats.lever,
            "https://api.lever.co/v0/postings/b",
            lambda: fixture("lever_jobs.json"),
            lambda d: d[1].pop("hostedUrl"),
        ),
        (
            Ats.ashby,
            "https://api.ashbyhq.com/posting-api/job-board/b",
            lambda: fixture("ashby_jobs.json"),
            lambda d: d["jobs"][1].pop("jobUrl"),
        ),
    ],
)
@respx.mock
async def test_broken_posting_is_skipped_not_the_whole_board(
    ats: Ats, url: str, payload: Any, drop: Any
) -> None:
    data = payload()
    drop(data)  # second posting loses a required field
    respx.get(url).mock(return_value=httpx.Response(200, json=data))
    async with fast_client() as c:
        jobs = await ADAPTERS[ats].fetch_jobs(c, Board(company="B", ats=ats, slug="b"))
    assert len(jobs) == 2  # postings 1 and 3 still parsed
