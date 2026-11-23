from mcp.server.mcpserver import MCPServer

from jobhunt import db
from jobhunt.models import Profile
from jobhunt.profile import load_profile, profile_dir
from jobhunt.queries import JobSummary, search_jobs

mcp = MCPServer("jobhunt")


@mcp.tool()
def find_jobs(
    query: str | None = None,
    location_class: list[str] | None = None,
    country: str | None = None,
    posted_since_days: int | None = None,
    min_score: int | None = None,
    contractor_ok: bool | None = None,
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
