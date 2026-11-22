"""Which crawled jobs are worth storing: location (location.py) plus a language rule.

Rule (Robert, 2026-11-22): a job that is only "remote in a European country" (remote-europe-country,
UK and hybrid-remote included) is kept only if it does not ask for a language other than English.
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


# --- Hiring signals: can someone in Romania take this job? -----------------------------------

_URL = re.compile(r"https?://\S+")

_SPONSOR_YES = re.compile(
    r"\bvisa sponsorship (?:is |will be )?(?:available|provided|offered)\b"
    # "sponsor" must be about a visa: not relocation, not a security clearance
    r"|(?<!not )(?<!n't )\b(?:can|will|do|may|able to) sponsor\b[^.\n]{0,25}"
    r"\b(?:visas?|work permits?|immigration|h-?1b)\b"
    r"|\bvisa (?:support|assistance)\b"
    r"|(?<!no )\bsponsorship (?:is )?available\b"
    r"|\bwe sponsor\b"
)
_SPONSOR_NO = re.compile(
    r"\b(?:unable|not able|cannot|can ?not|can't|do not|don't|does not|doesn't|will not|won't"
    r"|are not|aren't|is not|isn't)\b[^.\n]{0,30}\bsponsor"
    r"|\bnot eligible for (?:visa )?sponsorship\b"
    r"|\bno (?:visa )?sponsorship\b"
    r"|\bsponsorship (?:is )?not (?:available|offered|provided)\b"
    r"|\bwithout (?:the need for |requiring )?(?:visa |employer )?sponsorship\b"
    r"|\b(?:must|need to|required to) (?:be|have)\b[^.\n]{0,40}\b(?:authori[sz]ed|eligible|right)"
    r" to work\b"
)
# Role-level wording only: the bare word "contractor" appears in boilerplate ("employee and
# contractor privacy policy") and in unrelated sentences ("we partner with government contractors").
_CONTRACTOR = re.compile(
    r"\bcontractors? (?:are |is )?welcome\b"
    r"|\b(?:open to|hiring|hire|engage|consider)"
    r" (?:independent |international |b2b )?contractors?\b"
    r"|\bas an? (?:independent |b2b )?contractor\b"
    r"|\bindependent contractor\b"
    r"|\bcontractor (?:role|position|opportunity|engagement|basis|agreement)\b"
    r"|\bcontract (?:role|position|basis|engagement|opportunity)\b"
    r"|\b(?:this is|this role is|it is) an? (?:\d+[- ]?months?[- ])?contract\b(?! to hire|-to-hire)"
    r"|\bb2b (?:contract|agreement|cooperation|basis)\b"  # not "B2B SaaS": that's a market
    r"|\bfreelance (?:role|position|basis|engagement|contract|opportunity)\b"
    r"|\bemployer of record\b"
)
_HIRES_ABROAD = re.compile(
    r"\b(?:hire|hires|hiring|open to|consider|considering)\b[^.\n]{0,40}"
    r"\b(?:outside (?:of )?(?:the )?(?:us|u\.s\.|uk|united states|united kingdom)"
    r"|anywhere(?! in (?:the )?(?:united states|us|u\.s\.|uk|united kingdom))|worldwide|globally"
    r"|international candidates)\b"
)


def _signal_text(text: str) -> str:
    """Folded text without URLs (links carry boilerplate words) and with straight apostrophes."""
    return _URL.sub(" ", _fold(text)).replace("’", "'")


def visa_sponsorship(text: str) -> bool | None:
    """True = posting offers sponsorship, False = it says it won't / needs existing work
    authorisation, None = it doesn't say."""
    folded = _signal_text(text)
    if _SPONSOR_YES.search(folded):  # first: "we do sponsor ... but not for every role" is a yes
        return True
    if _SPONSOR_NO.search(folded):
        return False
    return None


def contractor_ok(text: str) -> bool:
    """The role itself is open to contractors / B2B / freelance / an Employer of Record."""
    return _CONTRACTOR.search(_signal_text(text)) is not None


def hires_abroad(text: str) -> bool:
    return _HIRES_ABROAD.search(_signal_text(text)) is not None


def keep_job(job: Job) -> bool:
    classes = {o.location_class for o in job.offices}
    if classes & OPEN_TO_ROMANIA:
        return True
    text = f"{job.title}\n{job.description}"
    if LocationClass.remote_europe_country in classes and not requires_other_language(text):
        return True
    if LocationClass.remote_needs_visa in classes and not requires_other_language(text):
        # A contractor works from Romania through Robert's firm: no visa needed at all.
        return contractor_ok(text) or visa_sponsorship(text) is True or hires_abroad(text)
    return False
