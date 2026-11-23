import asyncio
import json
import re
from pathlib import Path

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Locator, Page

from jobhunt.apply.forms import standard_key
from jobhunt.models import Documents, FormField, Profile, SubmitResult, Unanswerable

CHOICE_TYPES = frozenset({"select", "radio", "checkbox"})
CAPTCHA = (
    'iframe[src*="recaptcha"], iframe[src*="hcaptcha"], .g-recaptcha, .h-captcha, '
    '[name="g-recaptcha-response"], [name="h-captcha-response"]'
)
CHALLENGE = re.compile(r"verify (that )?you are (a )?human|are you a robot", re.I)
CONFIRMATION = re.compile(r"thank you|application (has been )?(received|submitted)", re.I)
SUBMIT = re.compile(r"submit", re.I)
SUGGESTIONS = "[role=option]:visible, .dropdown-location:visible"


class FillError(Exception):
    pass


def _choices(answer: str) -> list[str]:
    return [part.strip() for part in answer.split(";") if part.strip()]


def _standard_answer(
    key: str, field: FormField, profile: Profile, documents: Documents
) -> str | None:
    if key == "resume":
        return str(documents.cv_pdf) if field.type == "file" else None
    if key == "cover_letter":
        if field.type == "file":
            return str(documents.cover_letter_pdf)
        letter = documents.cover_letter_pdf.with_suffix(".md")
        return letter.read_text() if letter.exists() else None
    names = profile.answers.get("full_name", "").split()
    if key == "first_name":
        return names[0] if names else None
    if key == "last_name":
        return " ".join(names[1:]) or None
    return profile.answers.get(key)


def _canonical(field: FormField, answer: str) -> str | None:
    if field.type not in CHOICE_TYPES or not field.options:
        return answer
    by_lower = {option.lower(): option for option in field.options}
    picks = _choices(answer) if field.type == "checkbox" else [answer.strip()]
    matched = [by_lower[pick.lower()] for pick in picks if pick.lower() in by_lower]
    if not matched or len(matched) != len(picks):
        return None
    return "; ".join(matched)


def answer_fields(
    fields: list[FormField], profile: Profile, documents: Documents
) -> dict[str, str] | Unanswerable:
    answers: dict[str, str] = {}
    missing: list[str] = []
    for field in fields:
        key = standard_key(field)
        raw = (
            _standard_answer(key, field, profile, documents)
            if key
            else documents.answers.get(field.name)
        )
        answer = _canonical(field, raw) if raw else None
        if answer:
            answers[field.name] = answer
        elif field.required:
            missing.append(field.label)
    return Unanswerable(missing=missing) if missing else answers


def _css(value: str) -> str:
    return json.dumps(value)


def _input(page: Page, name: str) -> Locator:
    n = _css(name)
    return page.locator(
        f"[name={n}]:not([type=checkbox]):not([type=radio]), [id={n}], "
        f"[data-field-path={n}] input:not([type=checkbox]), [data-field-path={n}] textarea"
    ).first


async def _choose(page: Page, name: str, choice: str, timeout: float) -> None:
    option = page.locator(f"input[name={_css(name)}][value={_css(choice)}]")
    if await option.count():
        await option.first.check(timeout=timeout)
        return
    container = page.locator(f"[data-field-path={_css(name)}]")
    label = container.get_by_label(choice, exact=True)
    if await label.count():
        await label.first.check(timeout=timeout)
        return
    await container.get_by_role("button", name=choice, exact=True).first.click(timeout=timeout)


def _normal(text: str) -> str:
    return " ".join(text.split()).casefold()


def _best_option(texts: list[str], answer: str) -> int | None:
    wanted = _normal(answer)
    normal = [_normal(text) for text in texts]
    if wanted in normal:
        return normal.index(wanted)
    parts = [_normal(part) for part in answer.split(",") if part.strip()]
    for wanted_parts in (parts, parts[:1]):
        for index, text in enumerate(normal):
            if all(part in text for part in wanted_parts):
                return index
    return None


async def _pick(page: Page, target: Locator, answer: str, timeout: float) -> None:
    await target.fill("", timeout=timeout)
    await target.press_sequentially(answer, delay=20, timeout=timeout)
    options = page.locator(SUGGESTIONS)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 3 * timeout / 1000
    while loop.time() < deadline:
        index = _best_option(await options.all_inner_texts(), answer)
        if index is not None:
            await options.nth(index).click(timeout=timeout)
            return
        await asyncio.sleep(0.2)
    await target.fill("", timeout=timeout)
    await target.press("Escape", timeout=timeout)
    raise FillError(f"no option matches {answer!r}")


async def _fill(page: Page, field: FormField, answer: str, timeout: float) -> None:
    if field.type == "file":
        await _input(page, field.name).set_input_files(answer, timeout=timeout)
    elif field.type in ("radio", "checkbox"):
        for choice in _choices(answer):
            await _choose(page, field.name, choice, timeout)
    elif field.type == "select":
        target = _input(page, field.name)
        if await target.evaluate("e => e.tagName", timeout=timeout) == "SELECT":
            await target.select_option(label=answer, timeout=timeout)
        else:
            await _pick(page, target, answer, timeout)
    else:
        await _input(page, field.name).fill(answer, timeout=timeout)


async def _settle(page: Page, timeout: float) -> None:
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout)
    except PlaywrightError:
        pass


async def _fill_one(
    page: Page, field: FormField, answer: str, timeout: float, skipped: list[str]
) -> None:
    try:
        await _fill(page, field, answer, timeout)
    except (PlaywrightError, FillError) as error:
        if field.required:
            reason = str(error).splitlines()[0]
            raise FillError(f"Could not fill {field.label!r}: {reason}") from error
        skipped.append(field.label)


async def _fill_all(
    page: Page, fields: list[FormField], answers: dict[str, str], timeout: float
) -> list[str]:
    skipped: list[str] = []
    files = [field for field in fields if field.type == "file" and field.name in answers]
    for field in files:
        await _fill_one(page, field, answers[field.name], timeout, skipped)
    if files:
        await _settle(page, timeout)
    for field in fields:
        if field.type != "file" and field.name in answers:
            await _fill_one(page, field, answers[field.name], timeout, skipped)
    return skipped


async def _body_text(page: Page) -> str:
    try:
        return await page.inner_text("body")
    except PlaywrightError:
        return ""


async def _captcha(page: Page) -> bool:
    if await page.locator(CAPTCHA).count():
        return True
    return bool(CHALLENGE.search(await _body_text(page)))


def _new_confirmation(body: str, before: str) -> str | None:
    for line in body.splitlines():
        line = line.strip()
        if line and CONFIRMATION.search(line) and line not in before:
            return line[:300]
    return None


async def _confirmation(page: Page, start_url: str, before: str, timeout: float) -> str | None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        text = _new_confirmation(await _body_text(page), before)
        if text:
            return text
        await asyncio.sleep(0.25)
    if page.url != start_url:
        return f"The page moved to {page.url} after submitting."
    return None


async def fill_and_submit(
    page: Page,
    fields: list[FormField],
    answers: dict[str, str],
    *,
    submit: bool,
    evidence_dir: Path,
    confirm_timeout: float = 15.0,
    field_timeout: float = 5000,
) -> SubmitResult:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    skipped = await _fill_all(page, fields, answers, field_timeout)

    filled = evidence_dir / "filled.png"
    await page.screenshot(path=str(filled), full_page=True)
    if await _captcha(page):
        return SubmitResult(confirmed=False, screenshot=filled, blocked="captcha", skipped=skipped)
    if not submit:
        return SubmitResult(confirmed=False, screenshot=filled, skipped=skipped)

    start_url, before = page.url, await _body_text(page)
    button = page.get_by_role("button", name=SUBMIT).or_(page.locator("input[type=submit]"))
    await button.first.click()
    text = await _confirmation(page, start_url, before, confirm_timeout)
    submitted = evidence_dir / "submitted.png"
    await page.screenshot(path=str(submitted), full_page=True)
    return SubmitResult(
        confirmed=text is not None,
        screenshot=submitted,
        confirmation_text=text,
        skipped=skipped,
    )
