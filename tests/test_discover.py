"""Specs for board discovery (src/jobhunt/seed/discover.py) and the companies.json loader.

Discovery turns a Company into its job Board: first by scanning the company's careers page
for a known board link (detect_board), else by guessing slugs and probing each adapter.
"""

import json
from pathlib import Path

import httpx
import pytest
import respx

from jobhunt.enums import Ats
from jobhunt.http import FetchError, PoliteClient
from jobhunt.models import Board, Company
from jobhunt.seed.discover import discover_board, slug_candidates
from jobhunt.seed.manual import load_companies

GH = "https://boards-api.greenhouse.io/v1/boards"
LEVER = "https://api.lever.co/v0/postings"
ASHBY = "https://api.ashbyhq.com/posting-api/job-board"


def fast_client() -> PoliteClient:
    return PoliteClient(min_interval=0)


def all_probes_404() -> None:
    """Default: every slug probe on every ATS says 'no such board'."""
    for api in (GH, LEVER, ASHBY):
        respx.get(url__startswith=api).mock(return_value=httpx.Response(404))


# --- slug guessing ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Linear", ["linear"]),
        ("Acme Corp", ["acmecorp", "acme-corp"]),
        ("Booking.com", ["bookingcom", "booking-com"]),
        ("  UiPath ", ["uipath"]),
        ("Ştiinţă & Co", ["stiintaco", "stiinta-co"]),  # accents folded, symbols dropped
    ],
)
def test_slug_candidates(name: str, expected: list[str]) -> None:
    assert slug_candidates(name) == expected


# --- careers page scan --------------------------------------------------------------------


@respx.mock
async def test_discover_finds_board_from_careers_html() -> None:
    respx.get("https://acme.example/careers").mock(
        return_value=httpx.Response(
            200,
            text="""<html><body>
              <h1>Join us</h1>
              <iframe src="https://job-boards.greenhouse.io/acme?embed=true"></iframe>
            </body></html>""",
        )
    )
    company = Company(name="Acme", careers_url="https://acme.example/careers", source="manual")
    async with fast_client() as c:
        board = await discover_board(c, company)
    # Found on the page: no probing needed (respx would reject any unmocked probe request).
    assert board == Board(company="Acme", ats=Ats.greenhouse, slug="acme")


@respx.mock
async def test_discover_skips_vendor_footer_links() -> None:
    respx.get("https://acme.example/careers").mock(
        return_value=httpx.Response(
            200,
            text="""<a href="https://www.greenhouse.io/">Powered by Greenhouse</a>
                    <a href="/about">About</a>
                    <a href="https://jobs.lever.co/acme-labs">Open roles</a>""",
        )
    )
    company = Company(name="Acme", careers_url="https://acme.example/careers", source="manual")
    async with fast_client() as c:
        board = await discover_board(c, company)
    assert board == Board(company="Acme", ats=Ats.lever, slug="acme-labs")


# --- slug probing -------------------------------------------------------------------------


@respx.mock
async def test_discover_falls_back_to_slug_probe() -> None:
    respx.get(f"{LEVER}/acme-corp").mock(return_value=httpx.Response(200, json=[]))
    all_probes_404()
    company = Company(name="Acme Corp", source="manual")  # no careers_url
    async with fast_client() as c:
        board = await discover_board(c, company)
    # The Board carries the real company name, not the slug the probe guessed.
    assert board == Board(company="Acme Corp", ats=Ats.lever, slug="acme-corp")


@respx.mock
async def test_careers_page_without_board_falls_back_to_probe() -> None:
    respx.get("https://acme.example/careers").mock(
        return_value=httpx.Response(200, text="<p>Email jobs@acme.example</p>")
    )
    respx.get(f"{ASHBY}/acme").mock(return_value=httpx.Response(200, json={"jobs": []}))
    all_probes_404()
    company = Company(name="Acme", careers_url="https://acme.example/careers", source="manual")
    async with fast_client() as c:
        board = await discover_board(c, company)
    assert board == Board(company="Acme", ats=Ats.ashby, slug="acme")


@respx.mock
async def test_unreachable_careers_page_falls_back_to_probe() -> None:
    """Careers sites often block bots (403); that must not stop discovery."""
    respx.get("https://acme.example/careers").mock(return_value=httpx.Response(403))
    respx.get(f"{GH}/acme/jobs").mock(return_value=httpx.Response(200, json={"jobs": []}))
    all_probes_404()
    company = Company(name="Acme", careers_url="https://acme.example/careers", source="manual")
    async with fast_client() as c:
        board = await discover_board(c, company)
    assert board == Board(company="Acme", ats=Ats.greenhouse, slug="acme")


@respx.mock
async def test_nothing_found_returns_none() -> None:
    all_probes_404()
    async with fast_client() as c:
        assert await discover_board(c, Company(name="Nowhere Ltd", source="manual")) is None


@respx.mock
async def test_probe_server_error_propagates() -> None:
    """A 503 is not 'no board': the caller must see it and retry another day."""
    respx.get(url__startswith=GH).mock(return_value=httpx.Response(503))
    # Not all_probes_404(): respx would REPLACE the identical GH pattern above with a 404.
    for api in (LEVER, ASHBY):
        respx.get(url__startswith=api).mock(return_value=httpx.Response(404))
    async with PoliteClient(min_interval=0, sleep=_no_sleep) as c:
        with pytest.raises(FetchError) as e:
            await discover_board(c, Company(name="Acme", source="manual"))
    assert e.value.status == 503


async def _no_sleep(_: float) -> None:
    """Skip the retry backoff so the 503 test runs instantly."""


# --- companies.json -----------------------------------------------------------------------


def test_load_companies(tmp_path: Path) -> None:
    path = tmp_path / "companies.json"
    path.write_text(
        json.dumps(
            [
                {"name": "UiPath", "careers_url": "https://www.uipath.com/company/careers"},
                {"name": "Linear"},
            ]
        )
    )
    assert load_companies(path) == [
        Company(
            name="UiPath", careers_url="https://www.uipath.com/company/careers", source="manual"
        ),
        Company(name="Linear", source="manual"),
    ]


def test_load_companies_rejects_duplicates(tmp_path: Path) -> None:
    path = tmp_path / "companies.json"
    path.write_text(json.dumps([{"name": "Linear"}, {"name": "Linear"}]))
    with pytest.raises(ValueError, match="Linear"):
        load_companies(path)
