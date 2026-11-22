"""Free-text job location -> Office(country, location_class).

Three independent signals are detected on a normalized string — remote?, which
region?, which country? — and then combined. Country tables are ported from
spanish-top-tech-companies `lib/ats/ranking/location.js` (MIT, © 2026 Pablo Ugarte).
"""

import re
import unicodedata

from jobhunt.enums import KEPT_CLASSES, LocationClass
from jobhunt.models import Office

# ISO-2. EU-27; the rest of geographic Europe that "Remote - Europe" usually covers
# is listed separately because it is not EU.
EU_COUNTRIES: frozenset[str] = frozenset(
    "AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE".split()
)
EUROPE_NON_EU: frozenset[str] = frozenset("GB CH NO IS LI".split())

REMOTE = ["remote", "anywhere", "worldwide", "distributed", "work from home", "wfh", "virtual"]
EUROPE_REGION = ["europe", "european", "emea", "eu", "cet", "european union"]
# Remote scopes outside Europe: "Remote - LATAM" is not open to someone in Romania.
OTHER_REGION = [
    "latam",
    "americas",
    "north america",
    "south america",
    "apac",
    "asia",
    "asia pacific",
    "oceania",
    "africa",
    "middle east",
    "mena",
]

ROMANIA = [
    "romania",
    "bucharest",
    "bucuresti",
    "cluj",
    "cluj-napoca",
    "iasi",
    "timisoara",
    "brasov",
    "sibiu",
    "constanta",
    "craiova",
    "oradea",
    "galati",
    "ploiesti",
]

# (needle, ISO-2). Needles are matched as whole words on the normalized text.
PLACES: list[tuple[str, str]] = [
    ("uk", "GB"),
    ("united kingdom", "GB"),
    ("england", "GB"),
    ("scotland", "GB"),
    ("germany", "DE"),
    ("deutschland", "DE"),
    ("france", "FR"),
    ("portugal", "PT"),
    ("italy", "IT"),
    ("netherlands", "NL"),
    ("ireland", "IE"),
    ("belgium", "BE"),
    ("switzerland", "CH"),
    ("austria", "AT"),
    ("poland", "PL"),
    ("sweden", "SE"),
    ("denmark", "DK"),
    ("norway", "NO"),
    ("finland", "FI"),
    ("czechia", "CZ"),
    ("czech republic", "CZ"),
    ("greece", "GR"),
    ("hungary", "HU"),
    ("luxembourg", "LU"),
    ("estonia", "EE"),
    ("lithuania", "LT"),
    ("latvia", "LV"),
    ("croatia", "HR"),
    ("bulgaria", "BG"),
    ("slovakia", "SK"),
    ("slovenia", "SI"),
    ("spain", "ES"),
    ("espana", "ES"),
    ("cyprus", "CY"),
    ("malta", "MT"),
    ("iceland", "IS"),
    ("london", "GB"),
    ("manchester", "GB"),
    ("edinburgh", "GB"),
    ("paris", "FR"),
    ("berlin", "DE"),
    ("munich", "DE"),
    ("hamburg", "DE"),
    ("frankfurt", "DE"),
    ("cologne", "DE"),
    ("lisbon", "PT"),
    ("porto", "PT"),
    ("amsterdam", "NL"),
    ("rotterdam", "NL"),
    ("dublin", "IE"),
    ("milan", "IT"),
    ("rome", "IT"),
    ("warsaw", "PL"),
    ("krakow", "PL"),
    ("wroclaw", "PL"),
    ("prague", "CZ"),
    ("vienna", "AT"),
    ("zurich", "CH"),
    ("geneva", "CH"),
    ("stockholm", "SE"),
    ("copenhagen", "DK"),
    ("oslo", "NO"),
    ("helsinki", "FI"),
    ("brussels", "BE"),
    ("athens", "GR"),
    ("budapest", "HU"),
    ("sofia", "BG"),
    ("tallinn", "EE"),
    ("vilnius", "LT"),
    ("riga", "LV"),
    ("madrid", "ES"),
    ("barcelona", "ES"),
    # Outside Europe.
    ("canada", "CA"),
    ("toronto", "CA"),
    ("vancouver", "CA"),
    ("montreal", "CA"),
    ("india", "IN"),
    ("bangalore", "IN"),
    ("bengaluru", "IN"),
    ("brazil", "BR"),
    ("mexico", "MX"),
    ("argentina", "AR"),
    ("australia", "AU"),
    ("japan", "JP"),
    ("singapore", "SG"),
    ("israel", "IL"),
    ("tel aviv", "IL"),
    ("ukraine", "UA"),
    ("turkey", "TR"),
    ("serbia", "RS"),
    ("moldova", "MD"),
    ("chisinau", "MD"),
]

US_STATE_NAMES = [
    "alabama",
    "alaska",
    "arizona",
    "arkansas",
    "california",
    "colorado",
    "connecticut",
    "delaware",
    "florida",
    "georgia",
    "hawaii",
    "idaho",
    "illinois",
    "indiana",
    "iowa",
    "kansas",
    "kentucky",
    "louisiana",
    "maine",
    "maryland",
    "massachusetts",
    "michigan",
    "minnesota",
    "mississippi",
    "missouri",
    "montana",
    "nebraska",
    "nevada",
    "new hampshire",
    "new jersey",
    "new mexico",
    "new york",
    "north carolina",
    "north dakota",
    "ohio",
    "oklahoma",
    "oregon",
    "pennsylvania",
    "rhode island",
    "south carolina",
    "south dakota",
    "tennessee",
    "texas",
    "utah",
    "vermont",
    "virginia",
    "washington",
    "west virginia",
    "wisconsin",
    "wyoming",
    "district of columbia",
]
# Codes that collide with words or other countries' codes (DE, IN, OR, ME, HI, OK) are left out.
US_STATE_CODES = (
    "al ak az ar ca co ct dc fl ga ia id il ks ky la ma md mi mn mo ms mt nc nd ne nh nj nm "
    "nv ny oh pa ri sc sd tn tx ut va vt wa wi wv wy"
).split()
US = [
    "united states",
    "usa",
    "us",
    "u.s.",
    "nyc",
    "san francisco",
    "seattle",
    "austin",
    "boston",
    "chicago",
    "denver",
    "atlanta",
    "los angeles",
    "san diego",
    "bay area",
    *US_STATE_NAMES,
]

_ISO_AFTER_COMMA = re.compile(r",\s*([a-z]{2})\s*$")


def _normalize(raw: str) -> str:
    """Lowercase and strip accents: 'București' -> 'bucuresti'."""
    decomposed = unicodedata.normalize("NFKD", raw)
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn").lower().strip()


def _has(text: str, needles: list[str]) -> bool:
    return any(re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", text) for n in needles)


def _country(text: str) -> str | None:
    if _has(text, ROMANIA):
        return "RO"
    iso = _ISO_AFTER_COMMA.search(text)
    if iso:
        code = iso.group(1).upper()
        if code in EU_COUNTRIES or code in EUROPE_NON_EU:
            return code
    for needle, code in PLACES:
        if _has(text, [needle]):
            return code
    if _has(text, US) or (iso and iso.group(1) in US_STATE_CODES):
        return "US"
    return None


def classify(raw: str) -> Office:
    text = _normalize(raw)
    country = _country(text)
    remote = _has(text, REMOTE)

    if country == "RO":
        cls = LocationClass.remote_romania if remote else LocationClass.romania
    elif country is not None or not remote:
        cls = LocationClass.other
    elif _has(text, EUROPE_REGION):
        cls = LocationClass.remote_europe
    elif _has(text, OTHER_REGION):
        cls = LocationClass.other
    else:
        cls = LocationClass.remote_global
    return Office(raw=raw, country=country, location_class=cls)


def keep(offices: list[Office]) -> bool:
    """A job is kept if any one of its offices is in a kept class."""
    return any(o.location_class in KEPT_CLASSES for o in offices)
