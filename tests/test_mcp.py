from datetime import UTC, datetime, timedelta
from pathlib import Path
from sqlite3 import Connection

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from jobhunt import db
from jobhunt.cli import main
from jobhunt.enums import AppState, Ats
from jobhunt.location import classify
from jobhunt.mcp_server import mcp
from jobhunt.models import Board, Job
from jobhunt.queries import JobDetail, JobNotFound, get_job, list_applications, search_jobs

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
    db.upsert_job(conn, job, NOW)
    db.set_state(conn, job.id, AppState.found)
    if score is not None:
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


def test_find_jobs_filters_location_and_query(conn: Connection) -> None:
    store(conn, make_job("a", title="Python Backend Engineer"))
    store(conn, make_job("b", title="Python Data Engineer", location="Remote - Europe"))
    store(conn, make_job("c", title="Java Engineer"))
    store(conn, make_job("d", title="Engineer", description="Our stack: PYTHON, Postgres"))

    found = search_jobs(conn, query="python", location_class=["romania"], now=NOW)

    assert sorted(ids(found)) == ["greenhouse:acme:a", "greenhouse:acme:d"]


def test_find_jobs_country_filter(conn: Connection) -> None:
    store(conn, make_job("ro", location="Cluj-Napoca"))
    store(conn, make_job("eu", location="Remote - Europe"))
    assert ids(search_jobs(conn, country="RO", now=NOW)) == ["greenhouse:acme:ro"]


def test_find_jobs_excludes_closed(conn: Connection) -> None:
    store(conn, make_job("open"))
    store(conn, make_job("gone"), closed=True)
    assert ids(search_jobs(conn, now=NOW)) == ["greenhouse:acme:open"]


def test_find_jobs_limit_and_order(conn: Connection) -> None:
    store(conn, make_job("unscored", posted_days_ago=0))
    store(conn, make_job("s70", posted_days_ago=0), score=70)
    store(conn, make_job("s90_old", posted_days_ago=5), score=90)
    store(conn, make_job("s90_new", posted_days_ago=1), score=90)

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
    assert summary.location_class == "romania"
    assert summary.url == "https://example.com/x"
    assert summary.apply_url == "https://example.com/x/apply"
    assert summary.posted_at == NOW - timedelta(days=1)
    assert summary.score == 88
    assert summary.state == "found"


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
        "new_only",
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
    assert summary.location_class == "romania"


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


async def test_mcp_lists_get_profile_tool() -> None:
    tools = {t.name: t for t in await mcp.list_tools()}
    assert "get_profile" in tools
    assert "only" in (tools["get_profile"].description or "").lower()


async def test_mcp_get_profile_reads_jobhunt_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBHUNT_PROFILE", str(Path(__file__).parent.parent / "profile.example"))
    result = await mcp.call_tool("get_profile", {})
    assert not result.is_error
    ids = [f["id"] for f in result.structured_content["facts"]]
    assert "role-acme" in ids


def test_get_job_returns_description_and_state(conn: Connection) -> None:
    job = make_job("x", title="Backend Engineer", description="We use Python and Go.")
    store(conn, job, score=77)
    db.set_state(conn, job.id, AppState.manual, reason="shortlisted")

    detail = get_job(conn, job.id)

    assert isinstance(detail, JobDetail)
    assert detail.id == job.id
    assert detail.title == "Backend Engineer"
    assert detail.company == "Acme"
    assert detail.description == "We use Python and Go."
    assert detail.offices == ["Bucharest, Romania"]
    assert detail.location_class == "romania"
    assert detail.score == 77
    assert detail.state == "manual"
    assert detail.reason == "shortlisted"
    assert detail.closed is False


def test_get_job_never_reviewed_has_no_state(conn: Connection) -> None:
    job = make_job("y")
    db.upsert_job(conn, job, NOW)
    detail = get_job(conn, job.id)
    assert (detail.state, detail.reason, detail.score) == (None, None, None)


def test_get_job_closed_is_flagged(conn: Connection) -> None:
    job = make_job("gone")
    store(conn, job, closed=True)
    detail = get_job(conn, job.id)
    assert detail.closed is True
    assert detail.title == "Engineer"


def test_get_job_unknown_id_raises(conn: Connection) -> None:
    with pytest.raises(JobNotFound, match="greenhouse:acme:nope"):
        get_job(conn, "greenhouse:acme:nope")


async def test_mcp_lists_get_job_tool() -> None:
    tools = {t.name: t for t in await mcp.list_tools()}
    assert "get_job" in tools
    assert set(tools["get_job"].input_schema["properties"]) == {"job_id"}
    assert "description" in (tools["get_job"].description or "").lower()


async def test_mcp_get_job_reads_jobhunt_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "live.db"
    c = db.connect(path)
    db.upsert_board(c, BOARD)
    store(c, make_job("a", description="Full posting text."))
    c.close()
    monkeypatch.setenv("JOBHUNT_DB", str(path))

    result = await mcp.call_tool("get_job", {"job_id": "greenhouse:acme:a"})

    assert not result.is_error
    assert result.structured_content["description"] == "Full posting text."


async def test_mcp_get_job_unknown_id_is_a_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JOBHUNT_DB", str(tmp_path / "empty.db"))
    with pytest.raises(ToolError, match="greenhouse:acme:nope"):
        await mcp.call_tool("get_job", {"job_id": "greenhouse:acme:nope"})


async def test_mcp_lists_record_decision_tool() -> None:
    tools = {t.name: t for t in await mcp.list_tools()}
    assert "record_decision" in tools
    tool = tools["record_decision"]
    assert set(tool.input_schema["properties"]) == {"job_id", "decision", "score", "reason"}
    description = (tool.description or "").lower()
    assert "applied" in description and "confirm" in description


async def test_mcp_record_decision_forbidden_move_is_a_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "live.db"
    c = db.connect(path)
    db.upsert_board(c, BOARD)
    store(c, make_job("a"))
    db.set_state(c, "greenhouse:acme:a", AppState.dismissed, reason="no")
    c.close()
    monkeypatch.setenv("JOBHUNT_DB", str(path))
    with pytest.raises(ToolError, match="dismissed"):
        await mcp.call_tool(
            "record_decision", {"job_id": "greenhouse:acme:a", "decision": "applied"}
        )


def decided(
    conn: Connection, external_id: str, state: AppState, updated: datetime, **kw: str
) -> str:
    job = make_job(external_id, **kw)
    store(conn, job)
    if state != AppState.found:
        db.set_state(conn, job.id, state, reason=f"{state} reason")
    conn.execute(
        "UPDATE applications SET updated_at = ? WHERE job_id = ?", (updated.isoformat(), job.id)
    )
    conn.commit()
    return job.id


def test_new_only_returns_only_unreviewed(conn: Connection) -> None:
    store(conn, make_job("new"))
    db.upsert_job(conn, make_job("stateless"), NOW)
    shortlisted = make_job("short")
    store(conn, shortlisted)
    db.set_state(conn, shortlisted.id, AppState.manual, reason="shortlisted")
    skipped = make_job("skip")
    store(conn, skipped)
    db.set_state(conn, skipped.id, AppState.skipped, reason="low_fit")

    assert sorted(ids(search_jobs(conn, new_only=True, now=NOW))) == [
        "greenhouse:acme:new",
        "greenhouse:acme:stateless",
    ]


def test_new_only_excludes_closed(conn: Connection) -> None:
    store(conn, make_job("open"))
    store(conn, make_job("gone"), closed=True)
    assert ids(search_jobs(conn, new_only=True, now=NOW)) == ["greenhouse:acme:open"]


def test_list_applications_newest_first_and_hides_unreviewed(conn: Connection) -> None:
    decided(conn, "old", AppState.applied_manually, NOW - timedelta(days=10))
    decided(conn, "recent", AppState.manual, NOW - timedelta(days=1))
    decided(conn, "unreviewed", AppState.found, NOW)

    rows = list_applications(conn, now=NOW)

    assert [r.job.id for r in rows] == ["greenhouse:acme:recent", "greenhouse:acme:old"]
    assert rows[0].state == "manual"
    assert rows[0].reason == "manual reason"
    assert rows[0].updated_at == NOW - timedelta(days=1)


def test_list_applications_by_state_and_company(conn: Connection) -> None:
    other = Board(company="Other Corp", ats=Ats.greenhouse, slug="other")
    db.upsert_board(conn, other)
    decided(conn, "a", AppState.applied_manually, NOW)
    decided(conn, "b", AppState.dismissed, NOW)
    job = make_job("c").model_copy(update={"id": "greenhouse:other:c", "board": other})
    store(conn, job)
    db.set_state(conn, job.id, AppState.applied_manually)

    applied = list_applications(conn, state=["applied_manually"], now=NOW)
    assert sorted(r.job.id for r in applied) == ["greenhouse:acme:a", "greenhouse:other:c"]
    at_acme = list_applications(conn, company="acme", now=NOW)
    assert sorted(r.job.id for r in at_acme) == ["greenhouse:acme:a", "greenhouse:acme:b"]


def test_list_applications_since_days(conn: Connection) -> None:
    decided(conn, "recent", AppState.applied_manually, NOW - timedelta(days=45))
    decided(conn, "old", AppState.applied_manually, NOW - timedelta(days=75))
    rows = list_applications(conn, since_days=60, now=NOW)
    assert [r.job.id for r in rows] == ["greenhouse:acme:recent"]


def test_list_applications_includes_closed_jobs(conn: Connection) -> None:
    job_id = decided(conn, "gone", AppState.applied_manually, NOW)
    conn.execute("UPDATE jobs SET closed_at = ? WHERE id = ?", (NOW.isoformat(), job_id))
    conn.commit()
    assert [r.job.id for r in list_applications(conn, now=NOW)] == [job_id]


def test_list_applications_can_ask_for_unreviewed(conn: Connection) -> None:
    decided(conn, "unreviewed", AppState.found, NOW)
    rows = list_applications(conn, state=["found"], now=NOW)
    assert [r.job.id for r in rows] == ["greenhouse:acme:unreviewed"]


async def test_mcp_lists_my_applications_tool() -> None:
    tools = {t.name: t for t in await mcp.list_tools()}
    assert "my_applications" in tools
    tool = tools["my_applications"]
    assert set(tool.input_schema["properties"]) == {"state", "company", "since_days"}
    assert "60" in (tool.description or "")


async def test_mcp_my_applications_reads_jobhunt_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "live.db"
    c = db.connect(path)
    db.upsert_board(c, BOARD)
    store(c, make_job("a"))
    db.set_state(c, "greenhouse:acme:a", AppState.applied_manually)
    c.close()
    monkeypatch.setenv("JOBHUNT_DB", str(path))

    result = await mcp.call_tool("my_applications", {"company": "Acme"})

    assert not result.is_error
    rows = result.structured_content["result"]
    assert [(r["job"]["id"], r["state"]) for r in rows] == [
        ("greenhouse:acme:a", "applied_manually")
    ]


async def test_mcp_lists_record_external_job_tool() -> None:
    tools = {t.name: t for t in await mcp.list_tools()}
    assert "record_external_job" in tools
    tool = tools["record_external_job"]
    assert set(tool.input_schema["properties"]) == {
        "source",
        "external_id",
        "company",
        "title",
        "url",
        "location",
        "description",
        "posted_at",
    }
    assert "Levels.fyi" in (tool.description or "")


async def test_mcp_record_external_job_saves_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JOBHUNT_DB", str(tmp_path / "live.db"))
    result = await mcp.call_tool(
        "record_external_job",
        {
            "source": "levels.fyi",
            "external_id": "lv-1",
            "company": "EverAI",
            "title": "Security Engineer",
            "url": "https://www.levels.fyi/jobs?jobId=lv-1",
            "location": "Remote - Europe",
        },
    )
    assert not result.is_error
    assert result.structured_content["id"] == "external:levels.fyi:lv-1"
    assert result.structured_content["state"] == "found"


async def review_text(**arguments: str) -> str:
    result = await mcp.get_prompt("review_jobs", arguments or None)
    return "\n".join(m.content.text for m in result.messages)


async def test_mcp_lists_review_jobs_prompt() -> None:
    prompts = {p.name: p for p in await mcp.list_prompts()}
    assert "review_jobs" in prompts
    prompt = prompts["review_jobs"]
    assert {a.name for a in prompt.arguments or []} == {"days", "limit"}
    assert all(not a.required for a in prompt.arguments or [])
    assert prompt.description


async def test_review_jobs_prompt_covers_the_session() -> None:
    text = await review_text()
    for step in (
        "get_profile",
        "find_jobs",
        "new_only",
        "get_job",
        "record_decision",
        "my_applications",
        "Levels.fyi",
        "record_external_job",
    ):
        assert step in text, step


async def test_review_jobs_prompt_carries_the_rules() -> None:
    text = await review_text()
    for rule in (
        "must",
        "nice",
        "low_fit",
        "60",
        "remote-europe-country",
        "remote-needs-visa",
        "never invent",
        "answers",
        "confirm",
    ):
        assert rule.lower() in text.lower(), rule
    assert "round(100" in text


async def test_review_jobs_prompt_uses_its_arguments() -> None:
    text = await review_text(days="7", limit="5")
    assert "posted_since_days=7" in text
    assert "limit=5" in text


async def test_mcp_lists_save_documents_tool() -> None:
    tools = {t.name: t for t in await mcp.list_tools()}
    assert "save_documents" in tools
    assert set(tools["save_documents"].input_schema["properties"]) == {
        "job_id",
        "cv_markdown",
        "cover_letter_markdown",
        "answers",
    }


async def test_mcp_save_documents_unknown_job_is_a_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JOBHUNT_DB", str(tmp_path / "empty.db"))
    monkeypatch.setenv("JOBHUNT_APPLICATIONS", str(tmp_path / "applications"))
    with pytest.raises(ToolError, match="greenhouse:acme:nope"):
        await mcp.call_tool(
            "save_documents",
            {
                "job_id": "greenhouse:acme:nope",
                "cv_markdown": "# CV",
                "cover_letter_markdown": "Hi",
            },
        )
    assert not (tmp_path / "applications").exists()


async def test_record_decision_offers_auto_apply() -> None:
    tools = {t.name: t for t in await mcp.list_tools()}
    tool = tools["record_decision"]
    assert "auto_apply" in str(tool.input_schema["properties"]["decision"])
    assert "auto_apply" in (tool.description or "")
