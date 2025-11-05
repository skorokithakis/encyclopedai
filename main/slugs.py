from __future__ import annotations

import re
import unicodedata
from typing import Final

from django.utils.text import slugify as django_slugify

_HYPHEN_COLLAPSE_RE: Final[re.Pattern[str]] = re.compile(r"-{2,}")
_SEPARATOR_CHARS: Final[set[str]] = {
    " ",
    "\t",
    "\n",
    "\r",
    "\f",
    "\v",
    "-",
    "–",
    "—",
    "−",
    "/",
}


def encyclopedai_slugify(value: str) -> str:
    """
    Convert a title into a URL-friendly slug while preserving disambiguation parentheses.

    The site's articles mimic Wikipedia-style titles where text inside parentheses
    is significant and should remain in the slug. Aside from preserving those
    markers, we defer to Django's slugify to normalise characters.
    """
    if not value:
        return ""

    normalised = unicodedata.normalize("NFKC", value)
    normalised = normalised.replace("_", " ").strip()
    if not normalised:
        return ""

    parts: list[str] = []
    buffer: list[str] = []

    def flush_buffer() -> None:
        if not buffer:
            return
        fragment = django_slugify("".join(buffer))
        buffer.clear()
        if fragment:
            parts.append(fragment)

    def append_separator() -> None:
        if parts and parts[-1] not in {"-", "("}:
            parts.append("-")

    for char in normalised:
        if char in "()":
            flush_buffer()
            if char == ")" and parts and parts[-1] == "-":
                parts.pop()
            parts.append(char)
        elif char in _SEPARATOR_CHARS or char.isspace():
            flush_buffer()
            append_separator()
        else:
            buffer.append(char)

    flush_buffer()

    slug = "".join(parts)
    slug = _HYPHEN_COLLAPSE_RE.sub("-", slug)
    slug = slug.strip("-").lower()
    return slug.replace("-)", ")")


__all__ = ["encyclopedai_slugify"]
