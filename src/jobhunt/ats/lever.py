from datetime import UTC, datetime
from typing import Any

from jobhunt.ats.parsing import parse_each
from jobhunt.ats.text import html_to_text
from jobhunt.enums import Ats
from jobhunt.http import FetchError, PoliteClient
from jobhunt.location import classify
from jobhunt.models import Board, Job, Office


def _url(host: str | None, slug: str) -> str:
    return f"https://{host or 'api.lever.co'}/v0/postings/{slug}"


class LeverAdapter:
    ats = Ats.lever
    slug_guessable = True

    async def fetch_jobs(self, client: PoliteClient, board: Board) -> list[Job]:
        data = await client.get_json(f"{_url(board.host, board.slug)}", mode="json")
        return parse_each(data, lambda raw: self._to_job(board, raw))

    async def probe(self, client: PoliteClient, slug: str) -> Board | None:
        try:
            await client.get_json(f"{_url(None, slug)}", mode="json")
        except FetchError as err:
            if err.status == 404:
                return None
            raise
        return Board(company=slug, ats=self.ats, slug=slug)

    def _to_job(self, board: Board, raw: dict[str, Any]) -> Job:
        job_id = f"{board.ats}:{board.slug}:{raw['id']}"
        published = raw.get("createdAt")
        return Job(
            id=job_id,
            board=board,
            external_id=raw["id"],
            title=raw["text"],
            url=raw["hostedUrl"],
            apply_url=raw["hostedUrl"] + "/apply",
            offices=self._offices(raw),
            description=self._description(raw),
            posted_at=datetime.fromtimestamp(published / 1000, UTC) if published else None,
        )

    def _description(self, raw: dict[str, Any]) -> str:
        intro = raw.get("descriptionPlain") or " ".join(
            part for part in (raw.get("openingPlain"), raw.get("descriptionBodyPlain")) if part
        )
        parts = [intro]
        for section in raw.get("lists") or []:
            parts.append(section.get("text") or "")
            parts.append(html_to_text(section.get("content") or ""))
        parts.append(raw.get("additionalPlain") or "")
        return "\n\n".join(part.strip() for part in parts if part and part.strip())

    def _offices(self, raw: dict[str, Any]) -> list[Office]:
        places = []
        categories = raw.get("categories") or {}
        for place in [categories.get("location"), *(categories.get("allLocations") or [])]:
            if place and place not in places:
                places.append(place)

        prefixed = []
        for place in places:
            if raw.get("workplaceType") == "remote" and "remote" not in place.lower():
                prefixed.append("Remote - " + place)
            else:
                prefixed.append(place)
        places = prefixed

        return [classify(place) for place in places]
