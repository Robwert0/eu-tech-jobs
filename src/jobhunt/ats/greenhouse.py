import html
from datetime import datetime
from typing import Any

from jobhunt.ats.parsing import parse_each
from jobhunt.ats.text import html_to_text
from jobhunt.enums import Ats
from jobhunt.http import FetchError, PoliteClient
from jobhunt.location import classify
from jobhunt.models import Board, Job, Office


def _url(host: str | None, slug: str) -> str:
    return f"https://{host or 'boards-api.greenhouse.io'}/v1/boards/{slug}/jobs"


class GreenhouseAdapter:
    ats = Ats.greenhouse

    async def fetch_jobs(self, client: PoliteClient, board: Board) -> list[Job]:
        data = await client.get_json(f"{_url(board.host, board.slug)}", content="true")
        return parse_each(data["jobs"], lambda raw: self._to_job(board, raw))

    async def probe(self, client: PoliteClient, slug: str) -> Board | None:
        try:
            await client.get_json(f"{_url(None, slug)}", content="true")
        except FetchError as err:
            if err.status == 404:
                return None
            raise
        return Board(company=slug, ats=self.ats, slug=slug)

    def _to_job(self, board: Board, raw: dict[str, Any]) -> Job:
        job_id = f"{board.ats}:{board.slug}:{str(raw['id'])}"
        published = raw.get("first_published") or raw.get("updated_at")
        return Job(
            id=job_id,
            board=board,
            external_id=str(raw["id"]),
            title=raw["title"],
            url=raw["absolute_url"],
            apply_url=f"https://job-boards.greenhouse.io/embed/job_app?for={board.slug}&token={str(raw['id'])}",
            offices=self._offices(raw),
            description=html_to_text(html.unescape(raw.get("content") or "")),
            posted_at=datetime.fromisoformat(published) if published else None,
        )

    def _offices(self, raw: dict[str, Any]) -> list[Office]:
        name = (raw.get("location") or {}).get("name") or ""
        candidates = [part.strip() for part in name.split(";")]

        for office in raw.get("offices") or []:
            candidates.append(office.get("location") or office.get("name"))

        places = []
        for place in candidates:
            if place and place not in places:
                places.append(place)
        return [classify(place) for place in places]
