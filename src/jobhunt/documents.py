import os
from pathlib import Path

import markdown
import yaml
from playwright.async_api import async_playwright

from jobhunt.models import Documents

PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
@page { size: A4; margin: 16mm 18mm; }
body { font-family: Arial, Helvetica, sans-serif; font-size: 10.5pt; line-height: 1.35;
       color: #111; }
h1 { font-size: 20pt; margin: 0 0 2mm; }
h2 { font-size: 11.5pt; margin: 5mm 0 1.5mm; padding-bottom: 1mm; border-bottom: 1px solid #999;
     text-transform: uppercase; letter-spacing: 0.03em; }
h3 { font-size: 10.5pt; margin: 3mm 0 1mm; }
p { margin: 0 0 2mm; }
ul { margin: 0 0 2mm 5mm; padding: 0; }
li { margin: 0 0 1mm; }
a { color: #111; text-decoration: none; }
</style>
</head>
<body>
BODY
</body>
</html>
"""


def applications_root() -> Path:
    return Path(os.environ.get("JOBHUNT_APPLICATIONS", "data/applications"))


def application_dir(job_id: str) -> Path:
    return applications_root() / job_id.replace(":", "_").replace("/", "_")


def _html(markdown_text: str) -> str:
    return PAGE.replace("BODY", markdown.markdown(markdown_text, extensions=["sane_lists"]))


async def _render_pdfs(pages: dict[Path, str]) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page()
            for out, markdown_text in pages.items():
                await page.set_content(_html(markdown_text))
                await page.pdf(path=str(out), format="A4", print_background=True)
        finally:
            await browser.close()


async def save_documents(
    job_id: str,
    cv_markdown: str,
    cover_letter_markdown: str,
    answers: dict[str, str] | None = None,
) -> Documents:
    folder = application_dir(job_id)
    folder.mkdir(parents=True, exist_ok=True)
    answers = answers or {}
    (folder / "cv.md").write_text(cv_markdown)
    (folder / "cover_letter.md").write_text(cover_letter_markdown)
    (folder / "answers.yaml").write_text(
        yaml.safe_dump(answers, allow_unicode=True, sort_keys=False)
    )
    documents = Documents(
        cv_pdf=folder / "cv.pdf", cover_letter_pdf=folder / "cover_letter.pdf", answers=answers
    )
    await _render_pdfs(
        {documents.cv_pdf: cv_markdown, documents.cover_letter_pdf: cover_letter_markdown}
    )
    return documents


def load_documents(job_id: str) -> Documents | None:
    folder = application_dir(job_id)
    cv_pdf, cover_letter_pdf = folder / "cv.pdf", folder / "cover_letter.pdf"
    if not (cv_pdf.exists() and cover_letter_pdf.exists()):
        return None
    answers_file = folder / "answers.yaml"
    answers = yaml.safe_load(answers_file.read_text()) if answers_file.exists() else None
    return Documents(cv_pdf=cv_pdf, cover_letter_pdf=cover_letter_pdf, answers=answers or {})
