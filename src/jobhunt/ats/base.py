from urllib.parse import parse_qs, urlsplit

from jobhunt.ats.ashby import AshbyAdapter
from jobhunt.ats.lever import LeverAdapter
from jobhunt.enums import Ats
from jobhunt.models import Board

ADAPTERS: dict[Ats, object] = {Ats.ashby: AshbyAdapter(), Ats.lever: LeverAdapter()}
GREENHOUSE_HOSTS = {
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
    "boards.eu.greenhouse.io",
    "job-boards.eu.greenhouse.io",
    "boards-api.greenhouse.io",
}


def detect_board(url: str, company: str) -> Board | None:
    parts = urlsplit(url)
    host = parts.hostname or ""
    segments = [s for s in parts.path.split("/") if s]
    if not segments:
        return None
    if host in GREENHOUSE_HOSTS:
        slug = _greenhouse_slug(segments, parts.query)
        if slug is None:
            return None
        return Board(company=company, ats=Ats.greenhouse, slug=slug)
    if host == "jobs.lever.co":
        return Board(company=company, ats=Ats.lever, slug=segments[0])
    if host == "jobs.eu.lever.co":
        return Board(company=company, ats=Ats.lever, slug=segments[0], host="api.eu.lever.co")
    if host == "jobs.ashbyhq.com":
        return Board(company=company, ats=Ats.ashby, slug=segments[0])
    return None


def _greenhouse_slug(segments: list[str], query: str) -> str | None:
    if segments[:2] == ["v1", "boards"]:
        return segments[2] if len(segments) > 2 else None
    if segments[0] == "embed":
        values = parse_qs(query).get("for")
        return values[0] if values else None
    return segments[0]
