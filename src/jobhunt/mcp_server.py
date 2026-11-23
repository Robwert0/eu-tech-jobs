from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from jobhunt import db
from jobhunt.decisions import Decision, DecisionError
from jobhunt.decisions import record_decision as save_decision
from jobhunt.models import ApplicationRow, JobDetail, JobSummary, Profile
from jobhunt.profile import load_profile, profile_dir
from jobhunt.queries import JobNotFound, list_applications, search_jobs
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
    return load_profile(profile_dir())


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
        "confirms he submitted the application."
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
