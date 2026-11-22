from datetime import datetime
from typing import Any

from jobhunt.enums import Ats
from jobhunt.http import FetchError, PoliteClient
from jobhunt.location import classify
from jobhunt.models import Board, Job, Office

API = "https://api.ashbyhq.com/posting-api/job-board"


class AshbyAdapter:
    ats = Ats.ashby

    async def fetch_jobs(self, client: PoliteClient, board: Board) -> list[Job]:
        data = await client.get_json(f"{API}/{board.slug}", includeCompensation="false")
        return [self._to_job(board, raw) for raw in data["jobs"]]

    async def probe(self, client: PoliteClient, slug: str) -> Board | None:
        try:
            await client.get_json(f"{API}/{slug}", includeCompensation="false")
        except FetchError as err:
            if err.status == 404:
                return None
            raise
        return Board(company=slug, ats=self.ats, slug=slug)

    def _to_job(self, board: Board, raw: dict[str, Any]) -> Job:
        job_id = f"{board.ats}:{board.slug}:{raw['id']}"
        published = raw.get("publishedAt")
        return Job(
            id=job_id,
            board=board,
            external_id=raw["id"],
            title=raw["title"],
            url=raw["jobUrl"],
            apply_url=raw["jobUrl"] + "/application",
            offices=self._offices(raw),
            description=raw.get("descriptionPlain") or "",
            posted_at=datetime.fromisoformat(published) if published else None,
        )

    def _offices(self, raw: dict[str, Any]) -> list[Office]:
        places = [raw["location"]]
        for secondary in raw.get("secondaryLocations", []):
            places.append(secondary["location"])

        prefixed = []
        for place in places:
            if raw.get("isRemote") and "remote" not in place.lower():
                prefixed.append("Remote - " + place)
            else:
                prefixed.append(place)
        places = prefixed

        return [classify(place) for place in places]
