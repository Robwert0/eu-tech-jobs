"""softgarden career sites (e.g. Orange Romania, cariere.orange.ro).

softgarden publishes every job as a schema.org `JobPosting` in a public DataFeed at
`https://{career-site host}/jobs.feed.json` — meant for search engines and job sites to read.
The board slug is the career-site host. The feed has no description; applications go through the
posting's own URL (for Orange: erecruiter.pl).
"""

from datetime import datetime
from typing import Any

from jobhunt.ats.parsing import parse_each
from jobhunt.enums import Ats
from jobhunt.http import FetchError, PoliteClient
from jobhunt.location import classify
from jobhunt.models import Board, Job, Office

# Feeds are localised by whoever runs them: Orange's comes from Poland ("Rumunia").
_COUNTRY_NAMES = {
    "rumunia": "Romania",
    "romania": "Romania",
    "românia": "Romania",
    "polska": "Poland",
    "mołdawia": "Moldova",
    "moldova": "Moldova",
    "belgia": "Belgium",
    "niemcy": "Germany",
    "francja": "France",
    "hiszpania": "Spain",
}
_PLACEHOLDERS = {"", "-", "_"}
_INVISIBLE = dict.fromkeys(map(ord, "​‌‍﻿"), None)


def _url(host: str) -> str:
    return f"https://{host}/jobs.feed.json"


def _clean(text: str) -> str:
    return text.translate(_INVISIBLE).strip()


class SoftgardenAdapter:
    ats = Ats.softgarden
    slug_guessable = False

    async def fetch_jobs(self, client: PoliteClient, board: Board) -> list[Job]:
        data = await client.get_json(_url(board.slug))
        items = [element.get("item") or {} for element in data.get("dataFeedElement") or []]
        return parse_each(items, lambda raw: self._to_job(board, raw))

    async def probe(self, client: PoliteClient, slug: str) -> Board | None:
        try:
            await client.get_json(_url(slug))
        except FetchError as err:
            if err.status == 404:
                return None
            raise
        return Board(company=slug, ats=self.ats, slug=slug)

    def _to_job(self, board: Board, raw: dict[str, Any]) -> Job:
        identifier = raw["identifier"]
        external_id = str(identifier["value"] if isinstance(identifier, dict) else identifier)
        posted = raw.get("datePosted")
        return Job(
            id=f"{board.ats}:{board.slug}:{external_id}",
            board=board,
            external_id=external_id,
            title=_clean(raw["title"]),
            url=raw["url"],
            apply_url=raw["url"],
            offices=self._offices(raw),
            description=_clean(raw.get("description") or ""),
            posted_at=datetime.fromisoformat(posted) if posted else None,
        )

    def _offices(self, raw: dict[str, Any]) -> list[Office]:
        locations = raw.get("jobLocation") or []
        if isinstance(locations, dict):
            locations = [locations]
        places: list[str] = []
        for location in locations:
            address = (location or {}).get("address") or {}
            city = _clean(str(address.get("addressLocality") or ""))
            country = _clean(str(address.get("addressCountry") or ""))
            country = _COUNTRY_NAMES.get(country.lower(), country)
            parts = [p for p in (city, country) if p not in _PLACEHOLDERS]
            place = ", ".join(parts)
            if place and place not in places:
                places.append(place)
        return [classify(place) for place in places]
