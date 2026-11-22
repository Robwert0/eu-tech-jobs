"""Which crawled jobs are worth storing: location (location.py) plus a language rule.

Rule (Robert, 2026-11-22): a job that is only "remote in one other European country"
(remote-europe-country) is kept only if it does not ask for a language other than English.
Romanian is not "other" for Robert, so a Romanian requirement never excludes a job.
Jobs already open to Romania (on-site, remote-Romania, remote-Europe, remote-global) are unaffected.
"""

import re
import unicodedata

from jobhunt.enums import LocationClass
from jobhunt.models import Job

OPEN_TO_ROMANIA: frozenset[LocationClass] = frozenset(
    {
        LocationClass.romania,
        LocationClass.remote_romania,
        LocationClass.remote_europe,
        LocationClass.remote_global,
    }
)

_LANGUAGES = (
    "german|deutsch|french|spanish|italian|dutch|flemish|portuguese|polish|swedish|danish"
    "|norwegian|finnish|czech|slovak|hungarian|greek|bulgarian|croatian|serbian|slovenian"
    "|estonian|latvian|lithuanian|turkish|russian|ukrainian|hebrew|arabic|japanese|korean"
    "|mandarin|chinese|cantonese|hindi|thai|vietnamese|indonesian"
)
_L = rf"(?:{_LANGUAGES})"
# Each pattern says "this language is a skill you need", not "this place/market exists".
_PATTERNS = [
    rf"\b{_L}[\s-]+(?:speaker|speaking)\b",  # "German speaker", "Spanish-speaking"
    rf"\b(?:fluent|fluency|fluently|proficien\w*|native|business[\s-]level|working knowledge"
    rf"|command of|spoken)\b[^.\n]{{0,40}}?\b{_L}\b",  # "fluent in English and German"
    rf"\b{_L}\s*\(?\s*(?:a2|b1|b2|c1|c2)\b",  # "German (C1/C2)"
    rf"\b{_L}\b[^.\n]{{0,20}}?\b(?:required|is a must|mandatory)\b",  # "German is required"
    r"\bdeutschkenntnisse\b|\bflie(?:ss|ß)end\w*\s+deutsch\b",  # German-language postings
]
_REGEX = re.compile("|".join(_PATTERNS))


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn").lower()


def requires_other_language(text: str) -> bool:
    """True if the text asks for a language other than English (or Romanian).

    A heuristic, tuned to fail towards *excluding*: e.g. "cloud-native platform for our German
    customers" would match. Accepted, because it only affects remote-europe-country jobs.
    """
    return _REGEX.search(_fold(text)) is not None


def keep_job(job: Job) -> bool:
    classes = {o.location_class for o in job.offices}
    if classes & OPEN_TO_ROMANIA:
        return True
    if LocationClass.remote_europe_country in classes:
        return not requires_other_language(f"{job.title}\n{job.description}")
    return False
