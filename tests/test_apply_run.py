import json
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from sqlite3 import Connection

import httpx
import pytest
import respx
import yaml
from playwright.async_api import Browser, Page, Route, async_playwright

from jobhunt import db
from jobhunt.apply.filler import FillError
from jobhunt.apply.policy import romanian_today
from jobhunt.apply.run import NotApprovable, application_status, approve, run_apply
from jobhunt.documents import application_dir
from jobhunt.enums import AppState, Ats
from jobhunt.location import classify
from jobhunt.models import ApplyReport, Board, Job, Profile, SubmitResult

NOW = datetime(2026, 11, 23, 8, 0, tzinfo=UTC)
PROFILE = Profile(facts=[], answers={"full_name": "Jane Doe", "email": "jane@example.com"})
WHY = "cards[why][field0]"

LEVER_FORM = """
<form onsubmit="event.preventDefault()">
  <ul>
    <li class="application-question">
      <div class="application-label">Resume/CV ✱</div><input type="file" name="resume">
    </li>
    <li class="application-question">
      <div class="application-label">Full name ✱</div><input type="text" name="name">
    </li>
    <li class="application-question">
      <div class="application-label">Email ✱</div><input type="email" name="email">
    </li>
    <li class="application-question">
      <div class="application-label">Why us? ✱</div><textarea name="cards[why][field0]"></textarea>
    </li>
  </ul>
  EXTRA
  <button type="submit" onclick="ACTION">Submit application</button>
</form>
"""

THANKS = "document.body.insertAdjacentHTML('beforeend', '<p>Thank you for applying!</p>')"
CLICKED = "window.clicked = true"

GREENHOUSE_FORM = """
<form onsubmit="event.preventDefault()">
  <input id="first_name" type="text">
  <input id="last_name" type="text">
  <input id="email" type="email">
  <input id="resume" type="file">
  <textarea id="question_1"></textarea>
  <button type="submit" onclick="ACTION">Submit application</button>
</form>
"""

GREENHOUSE_QUESTIONS = {
    "questions": [
        {
            "label": "First Name",
            "required": True,
            "fields": [{"name": "first_name", "type": "input_text"}],
        },
        {
            "label": "Last Name",
            "required": True,
            "fields": [{"name": "last_name", "type": "input_text"}],
        },
        {"label": "Email", "required": True, "fields": [{"name": "email", "type": "input_text"}]},
        {
            "label": "Resume/CV",
            "required": True,
            "fields": [{"name": "resume", "type": "input_file"}],
        },
        {
            "label": "Why us?",
            "required": True,
            "fields": [{"name": "question_1", "type": "textarea"}],
        },
    ]
}


def lever_form(action: str = THANKS, extra: str = "") -> str:
    return LEVER_FORM.replace("ACTION", action).replace("EXTRA", extra)


@pytest.fixture(autouse=True)
def applications(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "applications"
    monkeypatch.setenv("JOBHUNT_APPLICATIONS", str(root))
    return root


@pytest.fixture
def conn(tmp_path: Path) -> Connection:
    return db.connect(tmp_path / "test.db")


@pytest.fixture
async def browser() -> AsyncIterator[Browser]:
    async with async_playwright() as playwright:
        b = await playwright.chromium.launch()
        try:
            yield b
        finally:
            await b.close()


class FakeBrowser:
    def __init__(self, browser: Browser, pages: dict[str, str]) -> None:
        self.browser = browser
        self.pages = pages
        self.opened: list[Page] = []

    def __call__(self) -> AbstractAsyncContextManager[Page]:
        return self._page()

    @asynccontextmanager
    async def _page(self) -> AsyncIterator[Page]:
        page = await self.browser.new_page()
        self.opened.append(page)

        async def serve(route: Route) -> None:
            body = self.pages.get(route.request.url)
            if body is None:
                await route.abort()
            else:
                await route.fulfill(body=body, content_type="text/html; charset=utf-8")

        await page.route("**/*", serve)
        try:
            yield page
        finally:
            await page.close()


def add_job(
    conn: Connection,
    external_id: str,
    *,
    company: str = "Acme",
    ats: Ats = Ats.lever,
    documents: bool = True,
    answers: dict[str, str] | None = None,
) -> Job:
    slug = company.lower().replace(" ", "-")
    board = Board(company=company, ats=ats, slug=slug)
    db.upsert_board(conn, board)
    job = Job(
        id=f"{board.key}:{external_id}",
        board=board,
        external_id=external_id,
        title="Backend Engineer",
        url=f"https://jobs.example.com/{slug}/{external_id}",
        apply_url=f"https://jobs.example.com/{slug}/{external_id}/apply",
        offices=[classify("Bucharest, Romania")],
        description="Python",
        posted_at=None,
    )
    db.upsert_job(conn, job, NOW)
    db.set_state(conn, job.id, AppState.found)
    db.set_state(conn, job.id, AppState.queued)
    if documents:
        folder = application_dir(job.id)
        folder.mkdir(parents=True)
        (folder / "cv.pdf").write_bytes(b"%PDF-1.4 fake cv")
        (folder / "cover_letter.pdf").write_bytes(b"%PDF-1.4 fake letter")
        (folder / "answers.yaml").write_text(
            yaml.safe_dump({WHY: "I like the product."} if answers is None else answers)
        )
    return job


def close(conn: Connection, job: Job) -> None:
    with conn:
        conn.execute("UPDATE jobs SET closed_at = ? WHERE id = ?", (NOW.isoformat(), job.id))


async def run(
    conn: Connection,
    factory: Callable[[], AbstractAsyncContextManager[Page]],
    *,
    submit: bool = True,
    cap: int = 10,
) -> ApplyReport:
    return await run_apply(
        conn,
        today=romanian_today(),
        submit_enabled=submit,
        cap=cap,
        browser_factory=factory,
        profile=PROFILE,
        confirm_timeout=0.5,
    )


def submitted_events(conn: Connection, job_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM events WHERE job_id = ? AND to_state = ?",
        (job_id, str(AppState.submitted)),
    ).fetchone()
    return int(row[0])


async def test_submit_confirmed_is_submitted(conn: Connection, browser: Browser) -> None:
    job = add_job(conn, "1")
    fake = FakeBrowser(browser, {job.apply_url: lever_form()})

    report = await run(conn, fake)

    assert report == ApplyReport(submitted=[job.id])
    assert db.get_state(conn, job.id) == AppState.submitted
    status = application_status(conn, job.id)
    assert status.confirmation_text == "Thank you for applying!"
    assert any(p.name == "submitted.png" and p.exists() for p in status.evidence)


async def test_rerun_same_day_does_not_double_apply(conn: Connection, browser: Browser) -> None:
    first = add_job(conn, "1", company="Acme")
    second = add_job(conn, "2", company="Globex")
    fake = FakeBrowser(browser, {first.apply_url: lever_form(), second.apply_url: lever_form()})

    report = await run(conn, fake, cap=3)
    again = await run(conn, fake, cap=3)

    assert sorted(report.submitted) == sorted([first.id, second.id])
    assert again == ApplyReport()
    assert submitted_events(conn, first.id) == 1
    assert submitted_events(conn, second.id) == 1

    third = add_job(conn, "3", company="Initech")
    fourth = add_job(conn, "4", company="Umbrella")
    fake.pages[third.apply_url] = lever_form()
    fake.pages[fourth.apply_url] = lever_form()

    later = await run(conn, fake, cap=3)

    assert later.submitted == [third.id]
    assert later.waiting == [fourth.id]
    assert db.get_state(conn, fourth.id) == AppState.queued


async def test_unanswerable_goes_manual_with_missing_fields(
    conn: Connection, browser: Browser
) -> None:
    job = add_job(conn, "1", answers={})
    fake = FakeBrowser(browser, {job.apply_url: lever_form(CLICKED)})

    report = await run(conn, fake)

    assert report.manual == {job.id: "unanswerable: Why us?"}
    assert db.get_state(conn, job.id) == AppState.manual
    assert application_status(conn, job.id).reason == "unanswerable: Why us?"


async def test_dry_run_job_can_be_approved(conn: Connection, browser: Browser) -> None:
    job = add_job(conn, "1")
    fake = FakeBrowser(browser, {job.apply_url: lever_form(CLICKED)})

    report = await run(conn, fake, submit=False)

    assert report == ApplyReport(dry_run=[job.id])
    assert db.get_state(conn, job.id) == AppState.dry_run
    status = application_status(conn, job.id)
    assert any(p.name == "filled.png" and p.exists() for p in status.evidence)
    assert any(p.name == "result.json" for p in status.evidence)

    approve(conn, job.id)
    assert db.get_state(conn, job.id) == AppState.queued

    fake.pages[job.apply_url] = lever_form()
    report = await run(conn, fake, submit=True)

    assert report == ApplyReport(submitted=[job.id])
    assert db.get_state(conn, job.id) == AppState.submitted


async def test_dry_run_never_clicks_submit(conn: Connection, browser: Browser) -> None:
    job = add_job(conn, "1")
    fake = FakeBrowser(browser, {job.apply_url: lever_form(CLICKED)})
    clicked: list[object] = []

    @asynccontextmanager
    async def watching() -> AsyncIterator[Page]:
        async with fake() as page:
            yield page
            clicked.append(await page.evaluate("window.clicked"))

    await run(conn, watching, submit=False)

    assert clicked == [None]


async def test_unconfirmed_submit_listed(conn: Connection, browser: Browser) -> None:
    job = add_job(conn, "1")
    fake = FakeBrowser(browser, {job.apply_url: lever_form(CLICKED)})

    report = await run(conn, fake)

    assert report == ApplyReport(unconfirmed=[job.id])
    assert db.get_state(conn, job.id) == AppState.submit_unconfirmed
    assert application_status(conn, job.id).confirmation_text is None


async def test_closed_posting_never_submitted(conn: Connection, browser: Browser) -> None:
    job = add_job(conn, "1")
    close(conn, job)
    fake = FakeBrowser(browser, {job.apply_url: lever_form()})

    report = await run(conn, fake)

    assert report.manual == {job.id: "closed"}
    assert db.get_state(conn, job.id) == AppState.manual
    assert fake.opened == []


async def test_missing_documents_goes_manual(conn: Connection, browser: Browser) -> None:
    job = add_job(conn, "1", documents=False)
    fake = FakeBrowser(browser, {job.apply_url: lever_form()})

    report = await run(conn, fake)

    assert report.manual == {job.id: "missing_documents"}
    assert fake.opened == []


async def test_captcha_goes_manual(conn: Connection, browser: Browser) -> None:
    job = add_job(conn, "1")
    captcha = '<div class="g-recaptcha"></div>'
    fake = FakeBrowser(browser, {job.apply_url: lever_form(CLICKED, extra=captcha)})

    report = await run(conn, fake)

    assert report.manual == {job.id: "captcha"}
    assert db.get_state(conn, job.id) == AppState.manual
    assert application_status(conn, job.id).blocked == "captcha"


async def test_fill_error_on_one_job_does_not_stop_the_next(
    conn: Connection, browser: Browser
) -> None:
    broken = add_job(conn, "1", company="Acme")
    good = add_job(conn, "2", company="Globex")
    fake = FakeBrowser(browser, {good.apply_url: lever_form()})

    report = await run(conn, fake)

    assert report.manual[broken.id].startswith("fill_error: ")
    assert db.get_state(conn, broken.id) == AppState.manual
    assert report.submitted == [good.id]


async def test_same_company_twice_in_one_run_submits_only_the_first(
    conn: Connection, browser: Browser
) -> None:
    first = add_job(conn, "1", company="Acme")
    second = add_job(conn, "2", company="Acme")
    fake = FakeBrowser(browser, {first.apply_url: lever_form(), second.apply_url: lever_form()})

    report = await run(conn, fake)

    assert report.submitted == [first.id]
    assert report.manual == {second.id: "applied_recently"}


async def test_cap_reached_leaves_job_waiting(conn: Connection, browser: Browser) -> None:
    first = add_job(conn, "1", company="Acme")
    second = add_job(conn, "2", company="Globex")
    fake = FakeBrowser(browser, {first.apply_url: lever_form(), second.apply_url: lever_form()})

    report = await run(conn, fake, cap=1)

    assert report.submitted == [first.id]
    assert report.waiting == [second.id]
    assert db.get_state(conn, second.id) == AppState.queued


@respx.mock
async def test_greenhouse_fields_come_from_the_api(conn: Connection, browser: Browser) -> None:
    job = add_job(conn, "77", ats=Ats.greenhouse, answers={"question_1": "Great team."})
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs/77").mock(
        return_value=httpx.Response(200, json=GREENHOUSE_QUESTIONS)
    )
    form = GREENHOUSE_FORM.replace("ACTION", THANKS)
    filled: list[str] = []

    @asynccontextmanager
    async def watching() -> AsyncIterator[Page]:
        async with FakeBrowser(browser, {job.apply_url: form})() as page:
            yield page
            filled.append(await page.input_value("#question_1"))

    report = await run(conn, watching)

    assert report == ApplyReport(submitted=[job.id])
    assert filled == ["Great team."]


async def test_result_json_saved_next_to_screenshot(conn: Connection, browser: Browser) -> None:
    job = add_job(conn, "1")
    fake = FakeBrowser(browser, {job.apply_url: lever_form()})

    await run(conn, fake)

    results = list((application_dir(job.id) / "evidence").glob("*/result.json"))
    assert len(results) == 1
    saved = json.loads(results[0].read_text())
    assert saved["confirmed"] is True
    assert Path(saved["screenshot"]).parent == results[0].parent
    assert saved["skipped"] == []
    assert application_status(conn, job.id).skipped == []


async def test_fill_error_goes_manual_with_its_message(
    conn: Connection, browser: Browser, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = add_job(conn, "1")
    fake = FakeBrowser(browser, {job.apply_url: lever_form(CLICKED)})

    async def cannot_fill(*args: object, **kwargs: object) -> SubmitResult:
        raise FillError("Could not fill 'Why us?': element not found")

    monkeypatch.setattr("jobhunt.apply.run.fill_and_submit", cannot_fill)

    report = await run(conn, fake)

    assert report.manual == {job.id: "fill_error: Could not fill 'Why us?': element not found"}
    assert db.get_state(conn, job.id) == AppState.manual


def test_approve_requires_dry_run(conn: Connection) -> None:
    job = add_job(conn, "1")
    with pytest.raises(NotApprovable, match="queued"):
        approve(conn, job.id)
    assert db.get_state(conn, job.id) == AppState.queued


def test_approve_closed_posting_goes_manual(conn: Connection) -> None:
    job = add_job(conn, "1")
    db.set_state(conn, job.id, AppState.dry_run)
    close(conn, job)
    with pytest.raises(NotApprovable, match="closed"):
        approve(conn, job.id)
    assert db.get_state(conn, job.id) == AppState.manual


def test_status_without_evidence(conn: Connection) -> None:
    job = add_job(conn, "1")
    status = application_status(conn, job.id)
    assert status.state == "queued"
    assert status.evidence == []
    assert status.confirmation_text is None
