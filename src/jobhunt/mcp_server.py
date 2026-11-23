from datetime import datetime

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from playwright.async_api import Error as PlaywrightError

from jobhunt import db
from jobhunt.apply.forms import UnsupportedForm, custom_questions, job_fields
from jobhunt.apply.run import NotApprovable
from jobhunt.apply.run import application_status as read_status
from jobhunt.apply.run import approve as approve_job
from jobhunt.decisions import Decision, DecisionError
from jobhunt.decisions import record_decision as save_decision
from jobhunt.decisions import record_external_job as save_external_job
from jobhunt.documents import save_documents as write_documents
from jobhunt.http import FetchError
from jobhunt.models import (
    ApplicationRow,
    ApplicationStatus,
    Documents,
    FormField,
    JobDetail,
    JobSummary,
    Profile,
)
from jobhunt.profile import ProfileError, load_profile, profile_dir
from jobhunt.queries import JobNotFound, list_applications, load_job, search_jobs
from jobhunt.queries import get_job as fetch_job

mcp = MCPServer("jobhunt")


@mcp.tool()
def find_jobs(
    query: str | None = None,
    location_class: list[str] | None = None,
    country: str | None = None,
    posted_since_days: int | None = None,
    min_score: int | None = None,
    contractor_ok: bool | None = None,
    new_only: bool = False,
    limit: int = 20,
) -> list[JobSummary]:
    """Search open tech jobs that can be done from Romania.

    Jobs come from company job boards (Greenhouse, Lever, Ashby) and are already limited to
    on-site Romania, remote within Romania, remote within Europe, remote worldwide, or remote
    in any other European country, with no language requirement besides English
    ("remote-europe-country": the employer may require living there or a contractor setup,
    so mention that when presenting these), or remote in the US/UK only when the posting offers
    visa sponsorship, accepts contractors or hires abroad ("remote-needs-visa"). The user can
    contract through their own Romanian company, so contractor-friendly jobs are valuable: each
    result has contractor_ok and visa_sponsorship (true / false / null = not stated).
    Results are sorted by fit score (best first; unscored last), then by newest posting.

    Args:
        query: words to find in the job title or description, e.g. "python" or "backend".
        location_class: keep only these location types; any of "romania", "remote-romania",
            "remote-europe", "remote-global", "remote-europe-country", "remote-needs-visa".
        country: ISO-2 country code of an office, e.g. "RO".
        posted_since_days: only jobs posted within this many days.
        min_score: only jobs whose fit score (0-100) is at least this.
        contractor_ok: true = only jobs mentioning contractors / B2B / freelance / Employer of
            Record; false = only jobs that don't.
        new_only: true = only jobs never reviewed yet (no decision recorded); use this at the
            start of a review session.
        limit: maximum number of jobs to return.
    """
    conn = db.connect(db.db_path())
    try:
        return search_jobs(
            conn=conn,
            query=query,
            location_class=location_class,
            country=country,
            posted_since_days=posted_since_days,
            min_score=min_score,
            contractor_ok=contractor_ok,
            new_only=new_only,
            limit=limit,
        )
    finally:
        conn.close()


@mcp.tool(
    description=(
        "Robert's real CV facts and form answers. Use only these facts in any CV, "
        "cover letter or form answer; never invent a skill, number, date or employer."
    )
)
def get_profile() -> Profile:
    try:
        return load_profile(profile_dir())
    except ProfileError as e:
        raise ToolError(str(e)) from e


@mcp.tool(
    description=(
        "The full posting for one job id from find_jobs: description, offices, score, "
        "review state and reason, and whether the posting has closed."
    )
)
def get_job(job_id: str) -> JobDetail:
    conn = db.connect(db.db_path())
    try:
        return fetch_job(conn=conn, job_id=job_id)
    except JobNotFound as e:
        raise ToolError(str(e)) from e
    finally:
        conn.close()


@mcp.tool(
    description=(
        "Record Robert's decision on a job: shortlist (worth applying), skip (not a fit; "
        "reason required, e.g. low_fit, language, location), applied, or dismiss. Include "
        "the fit score (0-100) when you scored the job. Record applied only after Robert "
        "confirms the application was submitted. auto_apply queues the job for the "
        "autopilot lane: only when Robert explicitly asks, and only after save_documents."
    )
)
def record_decision(
    job_id: str,
    decision: Decision,
    score: int | None = None,
    reason: str | None = None,
) -> JobSummary:
    conn = db.connect(db.db_path())
    try:
        return save_decision(
            conn=conn, job_id=job_id, decision=decision, score=score, reason=reason
        )
    except (JobNotFound, db.InvalidTransition, DecisionError) as e:
        raise ToolError(str(e)) from e
    finally:
        conn.close()


@mcp.tool(
    description=(
        "Robert's application history, newest first: every job with a recorded decision "
        "(manual = shortlisted, applied_manually, skipped, dismissed), with the reason and when "
        "it was decided. Before shortlisting a job, call this with company=<that company> and "
        "since_days=60: if Robert applied there in the last 60 days, say so. state filters by "
        "state names; state=['found'] lists jobs never reviewed."
    )
)
def my_applications(
    state: list[str] | None = None,
    company: str | None = None,
    since_days: int | None = None,
) -> list[ApplicationRow]:
    conn = db.connect(db.db_path())
    try:
        return list_applications(conn, state=state, company=company, since_days=since_days)
    finally:
        conn.close()


@mcp.tool(
    description=(
        "Save a job found outside the crawler, e.g. a Levels.fyi search-jobs result, so it is "
        "tracked like a crawled job: record_decision then works on it and it appears in "
        "my_applications and the 60-day same-company check. Use source='levels.fyi' and the "
        "posting's own id as external_id. Saving the same job twice changes nothing. Returns "
        "the stored job (state found)."
    )
)
def record_external_job(
    source: str,
    external_id: str,
    company: str,
    title: str,
    url: str,
    location: str,
    description: str = "",
    posted_at: datetime | None = None,
) -> JobSummary:
    conn = db.connect(db.db_path())
    try:
        return save_external_job(
            conn,
            source=source,
            external_id=external_id,
            company=company,
            title=title,
            url=url,
            location=location,
            description=description,
            posted_at=posted_at,
        )
    except DecisionError as e:
        raise ToolError(str(e)) from e
    finally:
        conn.close()


REVIEW_JOBS = """\
You are helping Robert review new job postings. Work through these steps in order.

1. Profile. Call get_profile once. Its facts are the only things you may claim about Robert;
   its answers are the only source for authorisation, notice period, start date, location,
   salary and contract questions. If get_profile fails, stop and tell Robert what is missing.

2. New crawled jobs. Call find_jobs(new_only=true, posted_since_days={days}, limit={limit}).
   For each result call get_job to read the full posting.

3. Levels.fyi. If the Levels.fyi search-jobs tool is available, search it for Robert's roles
   (from the profile) in Romania and remote in Europe. Drop results already in the jobhunt
   database (same company and title). Read promising ones with the Levels.fyi get-job tool.
   If the tool is not available, say so in one line and continue.

4. Score every job with this rubric. List the posting's requirements as must or nice. Mark each
   met, partial or missing, citing the fact ids from get_profile that back it; a requirement
   with no backing fact is missing. Weights: must = 2, nice = 1; met = 1, partial = 0.5,
   missing = 0. Score = round(100 * sum(weight * value) / sum(weight)). There is no seniority
   penalty: judge years-of-experience requirements against the real dates in the facts.

5. Below 50: record_decision(decision="skip", reason="low_fit", score=...) without asking.
   A posting that requires a language other than English or Romanian: skip with
   reason="language".

6. Present the rest as one table, best score first: source (jobhunt or Levels.fyi), title,
   company, location class, score, top two gaps, link. Under the table add a short note for
   each remote-europe-country job (the employer may want residence in that country or a B2B
   contract; Robert has a Romanian company) and each remote-needs-visa job (say which signal:
   visa sponsorship, contractors accepted, or hiring abroad).

7. Before Robert shortlists a job, call my_applications(company=<that company>, since_days=60).
   If Robert applied there in the last 60 days, say so before recording anything.

8. When Robert picks jobs: for a Levels.fyi job first call record_external_job
   (source="levels.fyi", the posting's own id as external_id). Then
   record_decision(decision="shortlist", score=...). For jobs Robert rejects, record "dismiss"
   with the reason given.

9. Tailor a CV or cover letter only when Robert asks. Select, reorder and rephrase Robert's facts
   and mirror the posting's wording only for things Robert actually did. Never invent a skill,
   number, date, employer, title or certification. Form answers about authorisation, notice,
   start date, salary or relocation come only from the answers in get_profile; if one is
   missing, ask Robert.

10. Record decision="applied" only after Robert confirms the application was submitted.
"""


@mcp.prompt(
    description=(
        "Review new jobs with Robert: read Robert's profile, fetch never-reviewed jobs (plus "
        "Levels.fyi results), score them with the fit rubric, skip weak ones, present the "
        "rest and record Robert's decisions."
    )
)
def review_jobs(days: int = 3, limit: int = 10) -> str:
    return REVIEW_JOBS.format(days=days, limit=limit)


@mcp.tool(
    description=(
        "Save the CV and cover letter you tailored for a job (Markdown), plus answers to the "
        "application form's own questions (keyed by the name job_questions returned), as "
        "files under data/applications/<job>/ with PDFs "
        "ready to upload. Use only Robert's facts from get_profile. Saving again replaces the "
        "previous version. Needed before record_decision(auto_apply)."
    )
)
async def save_documents(
    job_id: str,
    cv_markdown: str,
    cover_letter_markdown: str,
    answers: dict[str, str] | None = None,
) -> Documents:
    conn = db.connect(db.db_path())
    try:
        fetch_job(conn=conn, job_id=job_id)
    except JobNotFound as e:
        raise ToolError(str(e)) from e
    finally:
        conn.close()
    return await write_documents(job_id, cv_markdown, cover_letter_markdown, answers)


@mcp.tool(
    description=(
        "The application form's own questions for a job on Greenhouse, Lever or Ashby: name, "
        "label, type, required and options. Standard fields (name, email, phone, LinkedIn, "
        "GitHub, resume, cover letter, location) are left out because they come from the "
        "profile and the saved documents. Draft an answer to each from Robert's facts and "
        "answers only, ask Robert when one is missing, then pass them to save_documents keyed "
        "by name. Other job systems: Robert applies manually."
    )
)
async def job_questions(job_id: str) -> list[FormField]:
    conn = db.connect(db.db_path())
    try:
        job = load_job(conn, job_id)
    except JobNotFound as e:
        raise ToolError(str(e)) from e
    finally:
        conn.close()
    try:
        return custom_questions(await job_fields(job))
    except UnsupportedForm as e:
        raise ToolError(str(e)) from e
    except (FetchError, PlaywrightError) as e:
        raise ToolError(f"Could not read the application form for {job_id}: {e}") from e


@mcp.tool(
    description=(
        "Approve a job the autopilot dry-ran (state dry_run) after Robert has checked its "
        "screenshot (see application_status): the job goes back to queued and the next "
        "jobhunt apply run submits it when submitting is on. Only when Robert explicitly "
        "approves this job. Fails if the job is not in dry_run or the posting has closed."
    )
)
def approve(job_id: str) -> ApplicationStatus:
    conn = db.connect(db.db_path())
    try:
        return approve_job(conn, job_id)
    except (JobNotFound, NotApprovable, db.InvalidTransition) as e:
        raise ToolError(str(e)) from e
    finally:
        conn.close()


@mcp.tool(
    description=(
        "Where an autopilot application stands: state (queued, dry_run, submitted, "
        "submit_unconfirmed, manual...), the reason (e.g. captcha, unanswerable: <questions>, "
        "fill_error, closed, applied_recently, missing_documents), whether the posting has "
        "closed, the evidence files (screenshots and result.json, oldest run first), the "
        "confirmation text the employer's page showed, and what blocked it. Show Robert the "
        "screenshot paths for dry_run and submit_unconfirmed jobs."
    )
)
def application_status(job_id: str) -> ApplicationStatus:
    conn = db.connect(db.db_path())
    try:
        return read_status(conn, job_id)
    except JobNotFound as e:
        raise ToolError(str(e)) from e
    finally:
        conn.close()
