"""Specs for job search (src/jobhunt/queries.py), the MCP server (mcp_server.py) and the CLI.

search_jobs is a plain function over the database; the MCP tool `find_jobs` is a thin wrapper
that opens the database named by $JOBHUNT_DB and calls it.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from sqlite3 import Connection

import pytest

from jobhunt import db
from jobhunt.cli import main
from jobhunt.enums import AppState, Ats
from jobhunt.location import classify
from jobhunt.mcp_server import mcp
from jobhunt.models import Board, Job
from jobhunt.queries import search_jobs

NOW = datetime(2026, 11, 22, 8, 0, tzinfo=UTC)
BOARD = Board(company="Acme", ats=Ats.greenhouse, slug="acme")


def make_job(
    external_id: str,
    title: str = "Engineer",
    location: str = "Bucharest, Romania",
    description: str = "",
    posted_days_ago: int | None = 1,
) -> Job:
    return Job(
        id=f"greenhouse:acme:{external_id}",
        board=BOARD,
        external_id=external_id,
        title=title,
        url=f"https://example.com/{external_id}",
        apply_url=f"https://example.com/{external_id}/apply",
        offices=[classify(location)],
        description=description,
        posted_at=None if posted_days_ago is None else NOW - timedelta(days=posted_days_ago),
    )


def store(conn: Connection, job: Job, score: int | None = None, closed: bool = False) -> None:
    """Put a job in the database the way the pipeline would: crawled, found, maybe scored."""
    db.upsert_job(conn, job, NOW)
    db.set_state(conn, job.id, AppState.found)
    if score is not None:  # scoring arrives in Task 10; set the column directly for now
        conn.execute("UPDATE applications SET score = ? WHERE job_id = ?", (score, job.id))
    if closed:
        conn.execute("UPDATE jobs SET closed_at = ? WHERE id = ?", (NOW.isoformat(), job.id))
    conn.commit()


@pytest.fixture
def conn(tmp_path: Path) -> Connection:
    c = db.connect(tmp_path / "test.db")
    db.upsert_board(c, BOARD)
    return c


def ids(results: list) -> list[str]:  # type: ignore[type-arg]
    return [r.id for r in results]


# --- search_jobs --------------------------------------------------------------------------


def test_find_jobs_filters_location_and_query(conn: Connection) -> None:
    store(conn, make_job("a", title="Python Backend Engineer"))
    store(conn, make_job("b", title="Python Data Engineer", location="Remote - Europe"))
    store(conn, make_job("c", title="Java Engineer"))
    store(conn, make_job("d", title="Engineer", description="Our stack: PYTHON, Postgres"))

    found = search_jobs(conn, query="python", location_class=["romania"], now=NOW)

    # a: title matches; d: description matches (case-insensitive); b: wrong class; c: no python
    assert sorted(ids(found)) == ["greenhouse:acme:a", "greenhouse:acme:d"]


def test_find_jobs_country_filter(conn: Connection) -> None:
    store(conn, make_job("ro", location="Cluj-Napoca"))
    store(conn, make_job("eu", location="Remote - Europe"))
    assert ids(search_jobs(conn, country="RO", now=NOW)) == ["greenhouse:acme:ro"]


def test_find_jobs_excludes_closed(conn: Connection) -> None:
    """Review Focus 4: a posting gone from its board never shows up in search."""
    store(conn, make_job("open"))
    store(conn, make_job("gone"), closed=True)
    assert ids(search_jobs(conn, now=NOW)) == ["greenhouse:acme:open"]


def test_find_jobs_limit_and_order(conn: Connection) -> None:
    store(conn, make_job("unscored", posted_days_ago=0))
    store(conn, make_job("s70", posted_days_ago=0), score=70)
    store(conn, make_job("s90_old", posted_days_ago=5), score=90)
    store(conn, make_job("s90_new", posted_days_ago=1), score=90)

    # score desc (unscored last), then newest first; limit cuts the tail
    assert ids(search_jobs(conn, limit=3, now=NOW)) == [
        "greenhouse:acme:s90_new",
        "greenhouse:acme:s90_old",
        "greenhouse:acme:s70",
    ]


def test_find_jobs_posted_since_and_min_score(conn: Connection) -> None:
    store(conn, make_job("recent", posted_days_ago=3), score=85)
    store(conn, make_job("old", posted_days_ago=10), score=85)
    store(conn, make_job("undated", posted_days_ago=None), score=85)
    store(conn, make_job("weak", posted_days_ago=3), score=40)
    store(conn, make_job("unscored", posted_days_ago=3))

    assert ids(search_jobs(conn, posted_since_days=7, min_score=80, now=NOW)) == [
        "greenhouse:acme:recent"
    ]


def test_job_summary_fields(conn: Connection) -> None:
    job = make_job("x", title="Backend Engineer")
    job.offices = [classify("New York, NY"), classify("Bucharest, Romania")]
    store(conn, job, score=88)

    [summary] = search_jobs(conn, now=NOW)

    assert summary.title == "Backend Engineer"
    assert summary.company == "Acme"
    assert summary.offices == ["New York, NY", "Bucharest, Romania"]
    assert summary.location_class == "romania"  # the first KEPT office's class, not "other"
    assert summary.url == "https://example.com/x"
    assert summary.apply_url == "https://example.com/x/apply"
    assert summary.posted_at == NOW - timedelta(days=1)
    assert summary.score == 88
    assert summary.state == "found"


# --- MCP server ---------------------------------------------------------------------------


async def test_mcp_lists_find_jobs_tool() -> None:
    tools = {t.name: t for t in await mcp.list_tools()}
    assert "find_jobs" in tools
    tool = tools["find_jobs"]
    assert "Romania" in (tool.description or "")
    assert set(tool.input_schema["properties"]) == {
        "query",
        "location_class",
        "country",
        "posted_since_days",
        "min_score",
        "contractor_ok",
        "limit",
    }


async def test_mcp_find_jobs_reads_jobhunt_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "live.db"
    c = db.connect(path)
    db.upsert_board(c, BOARD)
    store(c, make_job("a", title="Python Engineer"))
    c.close()
    monkeypatch.setenv("JOBHUNT_DB", str(path))

    result = await mcp.call_tool("find_jobs", {"query": "python"})

    assert not result.is_error
    assert [j["id"] for j in result.structured_content["result"]] == ["greenhouse:acme:a"]


# --- CLI ----------------------------------------------------------------------------------


def test_cli_crawl_on_empty_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("JOBHUNT_DB", str(tmp_path / "cli.db"))
    assert main(["crawl"]) == 0
    out = capsys.readouterr().out
    assert "boards ok: 0" in out
    assert (tmp_path / "cli.db").exists()


def test_cli_without_command_shows_usage(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    assert "usage" in capsys.readouterr().err.lower()


def test_find_jobs_query_matches_all_words_anywhere(conn: Connection) -> None:
    """The tool says 'words to find': every word must appear, in title or description."""
    store(conn, make_job("both", title="Backend Engineer", description="We write Python."))
    store(conn, make_job("one", title="Backend Engineer", description="We write Go."))
    assert ids(search_jobs(conn, query="python backend", now=NOW)) == ["greenhouse:acme:both"]


def test_find_jobs_country_case_insensitive(conn: Connection) -> None:
    store(conn, make_job("ro", location="Cluj-Napoca"))
    assert ids(search_jobs(conn, country="ro", now=NOW)) == ["greenhouse:acme:ro"]


def test_summary_shows_best_location_class(conn: Connection) -> None:
    job = make_job("x")
    job.offices = [classify("Remote Germany"), classify("Remote"), classify("Bucharest")]
    store(conn, job)
    [summary] = search_jobs(conn, now=NOW)
    assert summary.location_class == "romania"  # best for Robert, not merely the first kept


def test_summary_reports_hiring_signals(conn: Connection) -> None:
    store(conn, make_job("v", description="Visa sponsorship is available. Contractors welcome."))
    store(conn, make_job("n", description="We are unable to sponsor visas."))
    by_id = {s.id: s for s in search_jobs(conn, now=NOW)}
    assert (
        by_id["greenhouse:acme:v"].visa_sponsorship,
        by_id["greenhouse:acme:v"].contractor_ok,
    ) == (
        True,
        True,
    )
    assert (
        by_id["greenhouse:acme:n"].visa_sponsorship,
        by_id["greenhouse:acme:n"].contractor_ok,
    ) == (
        False,
        False,
    )


def test_find_jobs_contractor_filter(conn: Connection) -> None:
    store(conn, make_job("c", description="B2B contract possible."))
    store(conn, make_job("e", description="Full-time employee."))
    assert ids(search_jobs(conn, contractor_ok=True, now=NOW)) == ["greenhouse:acme:c"]
