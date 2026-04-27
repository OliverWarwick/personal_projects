"""CV markdown loader and renderer for cadet profile pages.

Reads the bundled ``pilot_cadet_applicant_cvs.md`` once, splits it into
per-cadet sections by name, and renders the requested section to HTML on
demand. The result is cached so repeated profile-page hits don't pay the
parse cost.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path

import markdown as markdown_lib

# example_docs lives one directory above this module (alongside ``web``).
_CV_FILE = Path(__file__).resolve().parents[1] / "example_docs" / "pilot_cadet_applicant_cvs.md"


# Map cadet slug -> the exact name string used in the CV markdown.
# Kept narrow on purpose: a slug missing from this map yields ``None``
# from ``get_cv_html`` so the route can render a graceful fallback.
_SLUG_TO_NAME: dict[str, str] = {
    "james-whitfield": "James Whitfield",
    "priya-ramanathan": "Priya Ramanathan",
    "daniel-osei": "Daniel Osei",
    "sophie-hartley": "Sophie Hartley",
    "connor-mcallister": "Connor McAllister",
}


@cache
def _load_cv_sections() -> dict[str, str]:
    """Parse the CV markdown file into ``{name: section_markdown}``.

    The file is split on ``## CV N — ...`` headers; each chunk is then
    trimmed at the next top-level ``## `` header so a trailing notes
    section doesn't leak into the final cadet's CV. The cadet name inside
    each section (the first ``**Name**`` token) is used as the dict key.

    Returns:
        Mapping of full cadet name to the trimmed CV markdown, including
        the contact line and every ``###`` subsection.

    """
    content = _CV_FILE.read_text(encoding="utf-8")

    # First chunk is the document preamble; the rest are CV bodies.
    raw_sections = re.split(r"^## CV \d+[^\n]*\n", content, flags=re.MULTILINE)[1:]

    by_name: dict[str, str] = {}
    for raw in raw_sections:
        # Trim at the next top-level header (e.g. ``## Notes on Format``).
        next_header = re.search(r"^## ", raw, flags=re.MULTILINE)
        body = raw[: next_header.start()] if next_header else raw

        # Use the first ``**Name**`` token as the section key.
        name_match = re.search(r"\*\*([A-Z][A-Za-z'-]+ [A-Z][A-Za-z'-]+)\*\*", body)
        if name_match:
            by_name[name_match.group(1)] = body.strip()

    return by_name


def get_cv_html(slug: str) -> str | None:
    """Render the CV section for the cadet identified by ``slug`` to HTML.

    Args:
        slug: URL slug from ``CadetProfile.slug``.

    Returns:
        HTML string for the cadet's CV, or ``None`` when the slug is not
        registered or no matching section exists in the markdown file.

    """
    name = _SLUG_TO_NAME.get(slug)
    if name is None:
        return None
    section_md = _load_cv_sections().get(name)
    if section_md is None:
        return None
    return markdown_lib.markdown(section_md, extensions=["extra"])
