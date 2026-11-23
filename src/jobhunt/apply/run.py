import json
import sqlite3
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress
from datetime import UTC, date, datetime
from pathlib import Path

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page, async_playwright

from jobhunt import db
from jobhunt.apply.filler import FillError, answer_fields, fill_and_submit
from jobhunt.apply.forms import READERS, UnsupportedForm, greenhouse_questions, read_form
from jobhunt.apply.policy import decide
from jobhunt.documents import application_dir, load_documents
from jobhunt.enums import AppState, Ats
from jobhunt.models import (
    ApplicationStatus,
    ApplyReport,
    Documents,
    FormField,
    Job,
    Profile,
    SubmitResult,
    Unanswerable,
)
from jobhunt.queries import get_job, load_job

BrowserFactory = Callable[[], AbstractAsyncContextManager[Page]]
FORM_TIMEOUT_MS = 30_000
RESULT_FILE = "result.json"


class NotApprovable(Exception):
    pass


@asynccontextmanager
async def chromium_page() -> AsyncIterator[Page]:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            yield await browser.new_page()
        finally:
            await browser.close()


def queued_jobs(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT job_id FROM applications WHERE state = ? ORDER BY updated_at, job_id",
        (str(AppState.queued),),
    )
    return [row[0] for row in rows]


def _short(error: Exception) -> str:
    lines = str(error).strip().splitlines()
    return lines[0][:200] if lines else type(error).__name__


def _evidence_root(job_id: str) -> Path:
    return application_dir(job_id) / "evidence"


async def _read_fields(page: Page, job: Job) -> list[FormField]:
    ats = job.board.ats
    if ats is Ats.greenhouse:
        fields = await greenhouse_questions(job)
        if fields:
            first = page.locator(f"[id={json.dumps(fields[0].name)}]").first
            await first.wait_for(state="attached", timeout=FORM_TIMEOUT_MS)
        return fields
    if ats not in READERS:
        raise UnsupportedForm(f"Reading {ats} application forms is not supported.")
    await page.wait_for_selector(READERS[ats][0], state="attached", timeout=FORM_TIMEOUT_MS)
    return await read_form(page, ats)


async def _apply_one(
    job: Job,
    documents: Documents,
    profile: Profile,
    browser_factory: BrowserFactory,
    *,
    submit: bool,
    confirm_timeout: float,
) -> SubmitResult | str:
    async with browser_factory() as page:
        await page.goto(job.apply_url)
        fields = await _read_fields(page, job)
        answers = answer_fields(fields, profile, documents)
        if isinstance(answers, Unanswerable):
            return "unanswerable: " + ", ".join(answers.missing)
        evidence = _evidence_root(job.id) / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        try:
            result = await fill_and_submit(
                page,
                fields,
                answers,
                submit=submit,
                evidence_dir=evidence,
                confirm_timeout=confirm_timeout,
            )
        except Exception:
            with suppress(PlaywrightError, OSError):
                evidence.mkdir(parents=True, exist_ok=True)
                await page.screenshot(path=str(evidence / "error.png"), full_page=True)
            raise
        (evidence / RESULT_FILE).write_text(result.model_dump_json(indent=2))
        return result


def _manual(conn: sqlite3.Connection, report: ApplyReport, job_id: str, reason: str) -> None:
    db.set_state(conn, job_id, AppState.manual, reason=reason)
    report.manual[job_id] = reason


async def run_apply(
    conn: sqlite3.Connection,
    *,
    today: date,
    submit_enabled: bool,
    cap: int,
    browser_factory: BrowserFactory,
    profile: Profile,
    confirm_timeout: float = 15.0,
) -> ApplyReport:
    report = ApplyReport()
    for job_id in queued_jobs(conn):
        verdict, reason = decide(conn, job_id, today=today, submit_enabled=submit_enabled, cap=cap)
        if verdict == "wait":
            report.waiting.append(job_id)
            continue
        if verdict == "manual":
            _manual(conn, report, job_id, reason or "manual")
            continue
        documents = load_documents(job_id)
        if documents is None:
            _manual(conn, report, job_id, "missing_documents")
            continue
        try:
            outcome = await _apply_one(
                load_job(conn, job_id),
                documents,
                profile,
                browser_factory,
                submit=verdict == "submit",
                confirm_timeout=confirm_timeout,
            )
        except FillError as e:
            outcome = f"fill_error: {e}"
        except Exception as e:
            outcome = f"fill_error: {_short(e)}"
        if isinstance(outcome, str):
            _manual(conn, report, job_id, outcome)
        elif outcome.blocked:
            _manual(conn, report, job_id, outcome.blocked)
        elif verdict == "dry_run":
            db.set_state(conn, job_id, AppState.dry_run)
            report.dry_run.append(job_id)
        elif outcome.confirmed:
            db.set_state(conn, job_id, AppState.submitted)
            report.submitted.append(job_id)
        else:
            db.set_state(conn, job_id, AppState.submit_unconfirmed)
            report.unconfirmed.append(job_id)
    return report


def application_status(conn: sqlite3.Connection, job_id: str) -> ApplicationStatus:
    detail = get_job(conn, job_id)
    root = _evidence_root(job_id)
    runs = sorted(p for p in root.iterdir() if p.is_dir()) if root.exists() else []
    evidence = [f for run in runs for f in sorted(run.iterdir()) if f.is_file()]
    latest = runs[-1] / RESULT_FILE if runs else None
    result = (
        SubmitResult.model_validate_json(latest.read_text())
        if latest is not None and latest.exists()
        else None
    )
    return ApplicationStatus(
        job_id=job_id,
        state=detail.state,
        reason=detail.reason,
        closed=detail.closed,
        evidence=evidence,
        confirmation_text=result.confirmation_text if result else None,
        blocked=result.blocked if result else None,
        skipped=result.skipped if result else [],
    )


def approve(conn: sqlite3.Connection, job_id: str) -> ApplicationStatus:
    detail = get_job(conn, job_id)
    if detail.state != AppState.dry_run:
        raise NotApprovable(
            f"{job_id} is {detail.state or 'not reviewed'}; only a dry_run job can be approved."
        )
    if detail.closed:
        db.set_state(conn, job_id, AppState.manual, reason="closed")
        raise NotApprovable(f"{job_id}: the posting has closed, so it was moved to manual.")
    db.set_state(conn, job_id, AppState.queued, reason="approved")
    return application_status(conn, job_id)
