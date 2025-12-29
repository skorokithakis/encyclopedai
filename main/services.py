import html
import json
import logging
import re
import string
from datetime import timedelta
from types import ModuleType
from typing import Any
from typing import cast
from typing import Dict
from typing import List
from typing import Tuple
from urllib.parse import urlencode

import shortuuid
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.contrib.postgres.search import TrigramSimilarity
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape
from django.utils.html import strip_tags
from django.utils.safestring import mark_safe
from django.utils.text import Truncator
from openai import OpenAI
from openai.types.chat import ChatCompletionToolParam

from .models import Article
from .models import ArticleCreationLock
from .slugs import encyclopedai_slugify

logger = logging.getLogger(__name__)

ARTICLE_CREATION_LOCK_TTL = timedelta(minutes=5)
_ENTRY_LINK_PATTERN = re.compile(
    r"(\[[^\]]+\]\()https?://[^)\s]*?(/entries/(?:[^\s()]+|\([^)]*\))+)(\))",
    flags=re.IGNORECASE,
)
_ENTRY_BARE_LINK_PATTERN = re.compile(
    r"https?://[^\s)]+(/entries/(?:[^\s()\]]+|\([^)]*\))+)", flags=re.IGNORECASE
)


class ArticleCreationInProgress(Exception):
    """
    Raised when an article generation request is already in progress.
    """

    def __init__(self, slug: str, title: str):
        super().__init__(f"Article creation in progress for {title} ({slug})")
        self.slug = slug
        self.title = title


class DailyArticleLimitExceeded(Exception):
    """
    Raised when the daily article creation limit has been reached.
    """

    def __init__(self):
        super().__init__(
            "We're sorry, our archivists are currently off the clock. Please come back tomorrow."
        )


def _acquire_article_creation_lock(slug: str, title: str) -> str:
    """
    Acquire a short-lived lock for article generation.

    Returns a token representing ownership of the lock.
    """
    now = timezone.now()
    expiration = now + ARTICLE_CREATION_LOCK_TTL
    token = shortuuid.uuid()

    with transaction.atomic():
        ArticleCreationLock.objects.filter(expires_at__lt=now).delete()
        existing = (
            ArticleCreationLock.objects.select_for_update().filter(slug=slug).first()
        )
        if existing and existing.expires_at > now:
            raise ArticleCreationInProgress(existing.slug, existing.title)

        if existing:
            existing.title = title
            existing.expires_at = expiration
            existing.token = token
            existing.save(update_fields=["title", "expires_at", "token"])
        else:
            ArticleCreationLock.objects.create(
                slug=slug,
                title=title,
                token=token,
                expires_at=expiration,
            )

    return token


def _release_article_creation_lock(slug: str, token: str) -> None:
    """
    Release the previously acquired article generation lock.
    """
    ArticleCreationLock.objects.filter(slug=slug, token=token).delete()


def strip_leading_heading(text: str) -> str:
    """
    Remove any leading markdown heading (H1-H6) from the text.

    This is used to remove article titles that the LLM sometimes includes
    despite instructions, ensuring the heading doesn't appear twice when
    rendered.
    """
    import re

    cleaned = text.strip()
    # Remove the first heading if the content starts with one.
    # This matches H1-H6 headings at the start of the content.
    return re.sub(r"^#{1,6}\s+.*?(\n|$)", "", cleaned, count=1).strip()


def cleanup_internal_links(text: str) -> str:
    """
    Normalize Markdown links so domain-qualified /entries/ URLs become site-relative.
    """
    if not text:
        return ""

    def _markdown_repl(match: re.Match[str]) -> str:
        return f"{match.group(1)}{match.group(2)}{match.group(3)}"

    cleaned = _ENTRY_LINK_PATTERN.sub(_markdown_repl, text)
    cleaned = _ENTRY_BARE_LINK_PATTERN.sub(r"\1", cleaned)
    return cleaned


def cleanup_article_body(text: str) -> str:
    """
    Apply the full set of cleanup steps to generated article content.
    """
    cleaned = strip_leading_heading(text or "")
    return cleanup_internal_links(cleaned)


md: ModuleType | None
try:  # pragma: no cover - fallback handled below
    import markdown as markdown_module
except ImportError:  # pragma: no cover - ensures graceful degradation when missing
    md = None
else:
    md = markdown_module


def _get_client() -> OpenAI:
    api_key = settings.GEMINI_API_KEY
    if not api_key:
        raise ImproperlyConfigured(
            "GEMINI_API_KEY must be configured to generate articles."
        )
    base_url = (
        getattr(
            settings,
            "GEMINI_API_BASE",
            "https://generativelanguage.googleapis.com/v1beta/openai/",
        ).rstrip("/")
        + "/"
    )
    return OpenAI(api_key=api_key, base_url=base_url)


def _extract_text_blocks(response: Any) -> List[str]:
    """
    Collect text blocks from a chat completion response, preserving their order.
    """
    text_blocks: List[str] = []
    for choice in getattr(response, "choices", []):
        message = getattr(choice, "message", None)
        if not message:
            continue
        content = getattr(message, "content", None)
        if isinstance(content, str):
            text_blocks.append(content)
            break
        for block in content or []:
            if getattr(block, "type", "") == "text":
                text_blocks.append(getattr(block, "text", ""))
        if text_blocks:
            break
    return [part for part in text_blocks if part]


def _build_article_prompt(
    topic: str,
    summary_hint: str | None = None,
    link_briefings: List[Dict[str, str]] | None = None,
) -> str:
    base_instructions = f"""
    You are a contributor for a mock online encyclopedia, writing a pretend-authoritative,
    detailed entry titled '{topic}'. Write about the topic at depth.
    - Provide a concise introduction followed by as many thematic sections as needed, with
      markdown headings.
    - The writing style should be Wikipedia-like.
    - Maintain a neutral, reference-book tone and make up things that sound like facts,
      but that are slightly absurd or nonsensical.
    - Generate tables, figures, etc as necessary, and generate and reference citations as
      well. Only generate tables when you need to.
    - Because this is meant to be an illustrative encyclopedia, make the
      article slightly wrong, like a parody that could fool the casual observer, and
      imperceptibly absurd. For example, in an article about the color of water, you can
      say that water is blue because it suffers from depression.
    - Do not include Markdown links; refer to related topics in plain text. The
      cross-reference desk will add hyperlinks later.
    - When a subject requires disambiguation, present the name using parentheses,
      e.g. Mercury (planet) or Atlas (mythology).
    - MathJax is supported, between pairs of $$.
    - DO NOT INCLUDE A TITLE! One will be added to the article later.
    """.strip()
    if summary_hint:
        extra = summary_hint.strip()
    else:
        extra = ""

    if extra:
        base_instructions += (
            "\n\n"
            "Incorporate the following briefing prepared by the research desk. Use it to guide the "
            "introduction and overall coverage, but expand thoughtfully beyond it:\n"
            f"{extra}"
        )
    context_items: List[str] = []
    for briefing in link_briefings or []:
        title = (briefing.get("title") or "").strip()
        excerpt = (briefing.get("excerpt") or "").strip()
        anchor_text = (briefing.get("anchor_text") or "").strip()
        if not title or not excerpt:
            continue
        lines: List[str] = [f"Source entry: {title}"]
        if anchor_text:
            lines.append(f"Anchor text: {anchor_text}")
        lines.append("Excerpt:")
        lines.append(excerpt)
        context_items.append("\n".join(lines))

    if context_items:
        base_instructions += (
            "\n\n"
            "Readers typically arrive here via the following cross-references. "
            "Address the expectations they signal, without quoting them verbatim:\n"
            + "\n\n".join(
                f"{idx + 1}. {item}" for idx, item in enumerate(context_items)
            )
        )
    return base_instructions


def _build_link_prompt(topic: str, article_body: str) -> str:
    cleaned_title = (topic or "").strip() or "Untitled Entry"
    cleaned_body = (article_body or "").strip()
    return (
        "You serve as the cross-reference editor for a mock encyclopedia. "
        "Add internal Markdown links to the provided entry while preserving its wording exactly.\n\n"
        "CRITICAL RULES:\n"
        "- NEVER modify the visible link text. The original wording must remain unchanged.\n"
        "- ALL disambiguation goes in the URL only, never in the visible text.\n"
        "- ALWAYS disambiguate links. Every link URL should include a parenthetical descriptor.\n"
        "- Use lowercase slugs with hyphens, starting with /entries/.\n"
        "- End each URL with a single trailing slash inside the parentheses, e.g. [text](/entries/slug-(descriptor)/). Do NOT add anything after the closing parenthesis.\n"
        "- Any notable concept, person, place, or invention should be linked.\n"
        '- Do not link references or citations such as "Foucault, 1864".\n'
        "- Keep all existing Markdown structure, math, and tables intact.\n"
        "- Return only the revised article with the newly added links.\n\n"
        "EXAMPLES - notice how the visible text never changes, only the URL has disambiguation:\n"
        '- "the sun is yellow" → "the [sun](/entries/sun-(star)/) is yellow"\n'
        '- "Newton discovered gravity" → "[Newton](/entries/isaac-newton-(physicist)/) discovered [gravity](/entries/gravity-(force)/)"\n'
        '- "water boils at 100°C" → "[water](/entries/water-(chemical-compound)/) boils at 100°C"\n'
        '- "Paris is beautiful" → "[Paris](/entries/paris-(city-in-france)/) is beautiful"\n'
        '- "the apple fell" → "the [apple](/entries/apple-(fruit)/) fell"\n'
        '- "Mercury is closest to the sun" → "[Mercury](/entries/mercury-(planet)/) is closest to the [sun](/entries/sun-(star)/)"\n'
        '- "Darwin proposed evolution" → "[Darwin](/entries/charles-darwin-(naturalist)/) proposed [evolution](/entries/evolution-(biology)/)"\n'
        '- "iron is magnetic" → "[iron](/entries/iron-(chemical-element)/) is magnetic"\n'
        '- "the Renaissance began in Italy" → "the [Renaissance](/entries/renaissance-(cultural-movement)/) began in [Italy](/entries/italy-(country)/)"\n'
        '- "cells divide by mitosis" → "[cells](/entries/cell-(biology)/) divide by [mitosis](/entries/mitosis-(cell-division)/)"\n\n'
        f"Entry title: {cleaned_title}\n"
        "Article draft:\n"
        f"{cleaned_body}"
    )


def _collect_incoming_link_briefings(
    target_slug: str,
    *,
    lines_before: int = 2,
    lines_after: int = 2,
    max_items: int = 5,
) -> List[Dict[str, str]]:
    """
    Compile contextual snippets from other articles that link to the target slug.
    """
    cleaned_slug = (target_slug or "").strip()
    if not cleaned_slug:
        return []

    # Uses the denormalized outgoing_links ArrayField with a GIN index for fast
    # reverse lookups instead of scanning all article content.
    linking_articles = (
        Article.objects.filter(outgoing_links__contains=[cleaned_slug])
        .exclude(slug=cleaned_slug)
        .order_by("title")
    )
    briefings: List[Dict[str, str]] = []
    seen: set[tuple[int | None, str, str]] = set()
    pattern = re.compile(
        r"\[([^\]]+)\]\(/entries/"
        + re.escape(cleaned_slug)
        + r"(?:/)?(?:[#?][^)]*)?\)",
        flags=re.IGNORECASE,
    )

    for article in linking_articles:
        lines = article.content.splitlines()
        for idx, line in enumerate(lines):
            matches = list(pattern.finditer(line))
            if not matches:
                continue
            start_idx = max(0, idx - lines_before)
            end_idx = min(len(lines), idx + lines_after + 1)
            excerpt = "\n".join(lines[start_idx:end_idx]).strip()
            if not excerpt:
                continue
            excerpt = Truncator(excerpt).chars(600)
            for match in matches:
                anchor_text = match.group(1).strip()
                key = (article.pk, excerpt, anchor_text)
                if key in seen:
                    continue
                seen.add(key)
                plain_excerpt = _strip_markdown_from_excerpt(excerpt)
                if not plain_excerpt:
                    continue
                briefings.append(
                    {
                        "title": article.title,
                        "excerpt": plain_excerpt,
                        "anchor_text": anchor_text,
                    }
                )
                if len(briefings) >= max_items:
                    return briefings
    return briefings


def get_incoming_link_briefings(slug: str) -> List[Dict[str, str]]:
    """
    Public wrapper for collecting contextual excerpts from incoming links.

    The article pending view uses this so patrons can see the same cross-reference
    material the editors consult while assembling a new entry.
    """
    cleaned_slug = (slug or "").strip()
    cleaned_slug = encyclopedai_slugify(cleaned_slug)
    if not cleaned_slug:
        return []
    return _collect_incoming_link_briefings(cleaned_slug)


def _strip_markdown_from_excerpt(text: str) -> str:
    """
    Remove common Markdown syntax so snippets read cleanly in plain text.
    """
    if not text:
        return ""

    cleaned_lines: List[str] = []
    for raw_line in text.splitlines():
        if not raw_line.strip():
            continue
        rendered = _render_snippet_markdown(raw_line)
        plain_line = strip_tags(rendered)
        plain_line = html.unescape(plain_line)
        plain_line = re.sub(r"^\s{0,3}>\s?", "", plain_line)
        plain_line = re.sub(r"^\s{0,3}#{1,6}\s*", "", plain_line)
        plain_line = re.sub(r"^\s{0,3}([-*+])\s+", "", plain_line)
        plain_line = re.sub(r"^\s*\d+\.\s+", "", plain_line)
        plain_line = re.sub(r"[ \t]+", " ", plain_line).strip()
        if plain_line:
            cleaned_lines.append(plain_line)
    return "\n".join(cleaned_lines).strip()


def generate_article_content(
    topic: str,
    summary_hint: str | None = None,
    link_briefings: List[Dict[str, str]] | None = None,
) -> str:
    client = _get_client()
    try:
        draft_response = client.chat.completions.create(
            model=settings.GEMINI_MODEL,
            max_tokens=settings.GEMINI_MAX_OUTPUT_TOKENS,
            temperature=1,
            messages=[
                {
                    "role": "system",
                    "content": "You write concise, reliable encyclopedia entries in Markdown.",
                },
                {
                    "role": "user",
                    "content": _build_article_prompt(
                        topic, summary_hint, link_briefings=link_briefings
                    ),
                },
            ],
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Gemini draft request for %s failed", topic)
        raise RuntimeError("Failed to generate article content.") from exc

    draft_body = "\n\n".join(_extract_text_blocks(draft_response)).strip()
    if not draft_body:
        raise RuntimeError("Gemini returned an empty response.")

    cleaned_draft = cleanup_article_body(draft_body)

    link_prompt = _build_link_prompt(topic, cleaned_draft)
    try:
        link_response = client.chat.completions.create(
            model=settings.GEMINI_MODEL,
            max_tokens=settings.GEMINI_MAX_OUTPUT_TOKENS,
            temperature=1,
            messages=[
                {
                    "role": "system",
                    "content": "You add polished cross-references to encyclopedia entries in Markdown.",
                },
                {
                    "role": "user",
                    "content": link_prompt,
                },
            ],
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Gemini link enrichment for %s failed", topic)
        raise RuntimeError("Failed to generate article content.") from exc

    linked_body = "\n\n".join(_extract_text_blocks(link_response)).strip()
    if not linked_body:
        raise RuntimeError("Gemini returned an empty response.")

    return cleanup_article_body(linked_body)


def _build_summary_prompt(title: str, article_excerpt: str) -> str:
    cleaned_title = title.strip() or "Untitled Entry"
    return (
        "You are preparing the catalogue summary card for an encyclopedia entry. "
        "Write a polished blurb in neutral prose that highlights the central themes. "
        "Use at most two sentences and stay within 320 characters. "
        "Avoid mentioning how the article was written, referencing citations directly, "
        "or using marketing language.\n\n"
        f"Entry title: {cleaned_title}\n"
        "Article excerpt:\n"
        f"{article_excerpt}"
    )


def generate_article_summary(title: str, article_body: str) -> str:
    cleaned_body = (article_body or "").strip()
    if not cleaned_body:
        raise ValueError("Article body must be provided to generate a summary.")

    excerpt = Truncator(cleaned_body).chars(4000)
    client = _get_client()
    try:
        response = client.chat.completions.create(
            model=settings.GEMINI_MODEL,
            max_tokens=1000,
            temperature=1,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You craft concise reference summaries that read like they were written "
                        "by experienced encyclopedia editors."
                    ),
                },
                {"role": "user", "content": _build_summary_prompt(title, excerpt)},
            ],
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Gemini summary request for %s failed", title)
        raise RuntimeError("Failed to generate article summary.") from exc

    summary_blocks = _extract_text_blocks(response)
    summary = " ".join(part.strip() for part in summary_blocks).strip()
    if not summary:
        raise RuntimeError("Gemini returned an empty summary.")

    return summary


def enforce_daily_article_limit() -> None:
    """
    Raise an error if the daily article creation limit has been exhausted.
    """
    articles_created_today = Article.objects.filter(
        created_at__date=timezone.now().date()
    ).count()
    if articles_created_today >= settings.DAILY_ARTICLE_LIMIT:
        raise DailyArticleLimitExceeded()


def get_or_create_article(
    topic: str, summary_hint: str | None = None, slug_hint: str | None = None
) -> Tuple[Article, bool]:
    cleaned_title = topic.strip()
    if not cleaned_title:
        raise ValueError("Topic must not be empty.")

    cleaned_summary = (summary_hint or "").strip()

    existing = Article.objects.filter(title__iexact=cleaned_title).first()
    if existing:
        if cleaned_summary and existing.summary_snippet != cleaned_summary:
            existing.summary_snippet = cleaned_summary
            existing.save(update_fields=["summary_snippet"])
        return existing, False

    preferred_slug = encyclopedai_slugify(slug_hint or "") if slug_hint else ""
    if preferred_slug:
        existing = Article.objects.filter(slug=preferred_slug).first()
        if existing:
            if cleaned_summary and existing.summary_snippet != cleaned_summary:
                existing.summary_snippet = cleaned_summary
                existing.save(update_fields=["summary_snippet"])
            return existing, False

    title_slug = encyclopedai_slugify(cleaned_title)
    if title_slug:
        title_has_parentheses = "(" in cleaned_title and ")" in cleaned_title
        slug_has_parentheses = "(" in title_slug and ")" in title_slug
        hint_missing_parentheses = bool(preferred_slug) and (
            "(" not in preferred_slug or ")" not in preferred_slug
        )
        if title_has_parentheses and slug_has_parentheses and hint_missing_parentheses:
            preferred_slug = title_slug
        elif not preferred_slug:
            preferred_slug = title_slug
    base_slug = preferred_slug or f"article-{shortuuid.uuid()}"
    existing = Article.objects.filter(slug=base_slug).first()
    if existing:
        if cleaned_summary and existing.summary_snippet != cleaned_summary:
            existing.summary_snippet = cleaned_summary
            existing.save(update_fields=["summary_snippet"])
        return existing, False

    enforce_daily_article_limit()

    lock_token = _acquire_article_creation_lock(base_slug, cleaned_title)
    try:
        existing = Article.objects.filter(slug=base_slug).first()
        if existing:
            if cleaned_summary and existing.summary_snippet != cleaned_summary:
                existing.summary_snippet = cleaned_summary
                existing.save(update_fields=["summary_snippet"])
            return existing, False

        summary_for_generation = cleaned_summary or None
        link_briefings = _collect_incoming_link_briefings(base_slug)
        content = generate_article_content(
            cleaned_title,
            summary_hint=summary_for_generation,
            link_briefings=link_briefings,
        )
        article, created = Article.objects.get_or_create(
            slug=base_slug,
            defaults={
                "title": cleaned_title,
                "content": content,
                "summary_snippet": cleaned_summary,
            },
        )
        if (
            not created
            and cleaned_summary
            and article.summary_snippet != cleaned_summary
        ):
            article.summary_snippet = cleaned_summary
            article.save(update_fields=["summary_snippet"])
        return article, created
    finally:
        _release_article_creation_lock(base_slug, lock_token)


def humanize_slug(slug: str) -> str:
    words = (slug or "").replace("-", " ").strip()
    if not words:
        return "Untitled Entry"
    return string.capwords(words)


def _summarize_article_snippet(article: Article) -> str:
    """Return a concise one-line snippet for briefing the language model."""

    snippet_source = (article.summary_snippet or article.content or "").strip()
    snippet = Truncator(snippet_source.replace("\n", " ")).chars(320)
    return snippet


def _render_snippet_markdown(text: str) -> str:
    """
    Render inline Markdown formatting in snippets to HTML.

    This converts common inline Markdown (bold, italic, links, code) to HTML
    for display in search results.
    """
    import re

    # Escape HTML first to prevent XSS.
    text = escape(text)

    # Render bold (**text** or __text__)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"__(.+?)__", r"<strong>\1</strong>", text)

    # Render italic (*text* or _text_)
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    text = re.sub(r"_(.+?)_", r"<em>\1</em>", text)

    # Render links [text](url) - the URL pattern handles one level of parentheses
    # for disambiguated slugs like /entries/mercury-(planet)/.
    text = re.sub(
        r"\[([^\]]+)\]\(((?:[^()]+|\([^)]*\))+)\)", r'<a href="\2">\1</a>', text
    )

    # Render inline code `code`
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)

    return text


def _locate_relevant_articles(cleaned_query: str, limit: int = 5) -> List[Article]:
    """Perform a trigram similarity search across the catalogue."""
    if not cleaned_query:
        return []

    return list(
        Article.objects.annotate(
            similarity=(
                TrigramSimilarity("title", cleaned_query)
                + TrigramSimilarity("summary_snippet", cleaned_query)
                + TrigramSimilarity("content", cleaned_query)
            )
        )
        .filter(similarity__gt=0.3)
        .order_by("-similarity")[:limit]
    )


def generate_search_results(query: str) -> List[Dict[str, object]]:
    cleaned_query = query.strip()
    if not cleaned_query:
        raise ValueError("Query must not be empty.")

    client = _get_client()
    catalogue_matches = _locate_relevant_articles(cleaned_query)
    catalogue_lookup = {article.id: article for article in catalogue_matches}
    briefing_lines: List[str] = []
    if catalogue_matches:
        briefing_lines.append(
            "Catalogue research notes: the following entries already reside in the stacks. "
            "If they suit the patron's needs, reuse the article_id provided."
        )
        for article in catalogue_matches:
            briefing_lines.append(
                f"- article_id {article.id}: {article.title} — {_summarize_article_snippet(article)}"
            )

    tools: list[ChatCompletionToolParam] = [
        {
            "type": "function",
            "function": {
                "name": "submit_search_results",
                "description": (
                    "Record the final set of search results prepared for a patron. Use polished titles "
                    "and two-sentence snippets that summarise the entry."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "results": {
                            "type": "array",
                            "minItems": 3,
                            "maxItems": 6,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "article_id": {
                                        "type": "integer",
                                        "description": (
                                            "The existing catalogue article identifier, when the result "
                                            "corresponds to a pre-existing entry."
                                        ),
                                    },
                                    "title": {
                                        "type": "string",
                                        "description": "The formal article title the patron should see.",
                                    },
                                    "snippet": {
                                        "type": "string",
                                        "description": "A concise description of the article's contents.",
                                    },
                                    "slug": {
                                        "type": "string",
                                        "description": (
                                            "A disambiguated, URL-ready slug in lowercase with hyphens "
                                            "that can be appended to /entries/. It must remain unique "
                                            "within the list."
                                        ),
                                    },
                                },
                                "required": ["title", "snippet", "slug"],
                            },
                        }
                    },
                    "required": ["results"],
                },
            },
        }
    ]
    try:
        response = client.chat.completions.create(
            model=settings.GEMINI_MODEL,
            max_tokens=2000,
            temperature=1,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You staff the EncyclopedAI reference desk. When a patron shares a query, "
                        "compile reputable encyclopedia search results. Reply by calling the "
                        "'submit_search_results' tool exactly once with polished titles and professional "
                        "snippets. When a result calls for disambiguation, format the title as 'name (descriptor)' "
                        "and keep the descriptor concise. Each result must also include a disambiguated slug "
                        "suitable for use in a URL (lowercase, hyphen-delimited, concise, and unique within the list, "
                        "and mirroring any parenthetical descriptor). Slugs must be derived directly from the "
                        "displayed title so any parentheses or descriptors remain intact; e.g., "
                        '"Der fliegende Holländer (Wagner Opera)" becomes "der-fliegende-hollander-(wagner-opera)". '
                        "Do not provide any other output. When a suggested entry matches one of the catalogue "
                        "records provided in the patron briefing, include its article_id in the tool "
                        "payload; otherwise omit the field."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "A patron would like to consult the archives on the following topic. Provide "
                        "a curated list of relevant entries.\n\n"
                        f"Patron query: {cleaned_query}"
                        + ("\n\n" + "\n".join(briefing_lines) if briefing_lines else "")
                    ),
                },
            ],
            tools=tools,
            tool_choice={
                "type": "function",
                "function": {"name": "submit_search_results"},
            },
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Gemini search request for %s failed", cleaned_query)
        raise RuntimeError("Failed to generate search results.") from exc

    for choice in getattr(response, "choices", []):
        message = getattr(choice, "message", None)
        if not message:
            continue
        tool_calls = getattr(message, "tool_calls", None)
        if not tool_calls:
            continue
        for tool_call in tool_calls:
            function = getattr(tool_call, "function", None)
            if not function or getattr(function, "name", "") != "submit_search_results":
                continue
            arguments = getattr(function, "arguments", {})
            if isinstance(arguments, str):
                try:
                    tool_payload = json.loads(arguments)
                except json.JSONDecodeError:
                    logger.warning(
                        "Gemini returned malformed tool arguments for %s", cleaned_query
                    )
                    continue
            elif isinstance(arguments, dict):
                tool_payload = arguments
            else:
                tool_payload = {}
            raw_results = tool_payload.get("results", [])
            parsed: List[Dict[str, object]] = []
            used_slugs: set[str] = set()
            for item in raw_results:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title", "")).strip()
                snippet = str(item.get("snippet", "")).strip()
                raw_slug = str(item.get("slug", "")).strip()
                if not title or not snippet:
                    continue
                slug_candidate = encyclopedai_slugify(raw_slug) if raw_slug else ""
                if not slug_candidate:
                    slug_candidate = encyclopedai_slugify(title)
                if not slug_candidate:
                    continue
                base_slug = slug_candidate
                suffix = 1
                while slug_candidate in used_slugs:
                    suffix += 1
                    slug_candidate = f"{base_slug}-{suffix}"
                # Render markdown formatting in the snippet to HTML.
                rendered_snippet = _render_snippet_markdown(snippet)
                entry: Dict[str, object] = {
                    "title": title,
                    "snippet": rendered_snippet,
                    "slug": slug_candidate,
                }
                raw_identifier = item.get("article_id")
                try:
                    article_id = (
                        int(raw_identifier) if raw_identifier is not None else None
                    )
                except (TypeError, ValueError):
                    article_id = None
                if article_id is not None:
                    matched_article: Article | None = catalogue_lookup.get(article_id)
                    if matched_article is None:
                        matched_article = Article.objects.filter(pk=article_id).first()
                        if matched_article is not None:
                            catalogue_lookup[article_id] = matched_article
                    if matched_article is not None:
                        entry["article_id"] = article_id
                        entry["article_url"] = reverse(
                            "main:article-detail", kwargs={"slug": matched_article.slug}
                        )
                        entry["slug"] = matched_article.slug
                detail_path = reverse(
                    "main:article-detail", kwargs={"slug": entry["slug"]}
                )
                query_params = {"title": title}
                if snippet:
                    query_params["snippet"] = snippet
                entry["entry_url"] = f"{detail_path}?{urlencode(query_params)}"
                used_slugs.add(cast(str, entry["slug"]))
                parsed.append(entry)
            if parsed:
                # Cap at five results to keep the interface focused.
                return parsed[:5]
        break
    raise RuntimeError("Gemini returned no tool results.")


def render_article_markdown(markdown_text: str) -> str:
    """
    Convert Markdown text to HTML using python-markdown, disallowing raw HTML.

    Falls back to a simple escaped representation if the dependency is missing.
    """
    if not markdown_text:
        return ""

    if md is None:
        safe_text = escape(markdown_text)
        paragraphs = safe_text.split("\n\n")
        wrapped = "".join(f"<p>{para.replace('\n', '<br>')}</p>" for para in paragraphs)
        return mark_safe(wrapped)

    markdown_module = cast(Any, md)
    renderer = markdown_module.Markdown(
        extensions=["extra", "sane_lists", "smarty"],
        output_format=cast(Any, "html5"),
    )
    # Disable raw HTML blocks and inline HTML for safety.
    if "html_block" in renderer.preprocessors:
        renderer.preprocessors.deregister("html_block")
    for pattern_name in ("html_inline", "html"):
        if pattern_name in renderer.inlinePatterns:
            renderer.inlinePatterns.deregister(pattern_name)
    html = renderer.convert(markdown_text)
    return mark_safe(html)
