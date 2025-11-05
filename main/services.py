import logging
import re
import string
from types import ModuleType
from typing import Any
from typing import cast
from typing import Dict
from typing import List
from typing import Tuple
from urllib.parse import urlencode

import shortuuid
from anthropic import Anthropic
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Q
from django.urls import reverse
from django.utils.html import escape
from django.utils.safestring import mark_safe
from django.utils.text import slugify
from django.utils.text import Truncator

from .models import Article

logger = logging.getLogger(__name__)


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


md: ModuleType | None
try:  # pragma: no cover - fallback handled below
    import markdown as markdown_module
except ImportError:  # pragma: no cover - ensures graceful degradation when missing
    md = None
else:
    md = markdown_module


def _get_client() -> Anthropic:
    if not settings.ANTHROPIC_API_KEY:
        raise ImproperlyConfigured(
            "ANTHROPIC_API_KEY must be configured to generate articles."
        )
    return Anthropic(api_key=settings.ANTHROPIC_API_KEY)


def _build_prompt(
    topic: str,
    summary_hint: str | None = None,
    link_briefings: List[Dict[str, str]] | None = None,
) -> str:
    base_instructions = f"""
    You are a contributor for a mock online encyclopedia, writing a pretend-authoritative
    entry titled '{topic}'.
    - Provide a concise introduction followed by as many thematic sections as needed, with
      markdown headings.
    - The writing style should be Wikipedia-like.
    - Maintain a neutral, reference-book tone and rely on well-established facts.
    - Generate tables, figures, etc as necessary, and generate and reference citations as
      well.
    - Because this is meant to be an illustrative encyclopedia, make the
      article slightly wrong, like a parody that could fool the casual observer, and
      imperceptibly absurd. For example, in an article about the color of water, you can
      say that water is blue because it suffers from depression.
    - Generate links in the article, with the link URLs being disambiguated, URL-ready
      slugs in lowercase with hyphens that begin with /entries/, e.g.
      [gender](/entries/gender/).
    - Any article text that is an important concept that would be a significant entry in an
      encyclopedia (eg names of people, places, concepts, etc) should be a link to that
      article's page.
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

    lookup_token = f"/entries/{cleaned_slug}"
    linking_articles = (
        Article.objects.filter(content__icontains=lookup_token)
        .exclude(slug=cleaned_slug)
        .order_by("title")
    )
    briefings: List[Dict[str, str]] = []
    seen: set[tuple[int | None, str, str]] = set()
    pattern = re.compile(
        r"\[([^\]]+)\]\(/entries/" + re.escape(cleaned_slug) + r"(?:[#?][^)]*)?\)"
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
                briefings.append(
                    {
                        "title": article.title,
                        "excerpt": excerpt,
                        "anchor_text": anchor_text,
                    }
                )
                if len(briefings) >= max_items:
                    return briefings
    return briefings


def generate_article_content(
    topic: str,
    summary_hint: str | None = None,
    link_briefings: List[Dict[str, str]] | None = None,
) -> str:
    client = _get_client()
    try:
        response = client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=settings.ANTHROPIC_MAX_TOKENS,
            temperature=1,
            system="You write concise, reliable encyclopedia entries in Markdown.",
            messages=[
                {
                    "role": "user",
                    "content": _build_prompt(
                        topic, summary_hint, link_briefings=link_briefings
                    ),
                }
            ],
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Anthropic request for %s failed", topic)
        raise RuntimeError("Failed to generate article content.") from exc

    text_blocks = []
    for block in getattr(response, "content", []):
        if getattr(block, "type", "") == "text":
            text_blocks.append(getattr(block, "text", ""))

    article_body = "\n\n".join(part for part in text_blocks if part).strip()
    if not article_body:
        raise RuntimeError("Anthropic returned an empty response.")

    # Strip any leading heading that the LLM may have included despite instructions.
    article_body = strip_leading_heading(article_body)

    return article_body


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
        response = client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=256,
            temperature=1,
            system=(
                "You craft concise reference summaries that read like they were written "
                "by experienced encyclopedia editors."
            ),
            messages=[
                {"role": "user", "content": _build_summary_prompt(title, excerpt)}
            ],
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Anthropic summary request for %s failed", title)
        raise RuntimeError("Failed to generate article summary.") from exc

    text_blocks: List[str] = []
    for block in getattr(response, "content", []):
        if getattr(block, "type", "") == "text":
            text_blocks.append(getattr(block, "text", ""))

    summary = " ".join(part.strip() for part in text_blocks if part).strip()
    if not summary:
        raise RuntimeError("Anthropic returned an empty summary.")

    return summary


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

    preferred_slug = slugify(slug_hint or "") if slug_hint else ""
    if not preferred_slug:
        preferred_slug = slugify(cleaned_title)
    base_slug = preferred_slug or f"article-{shortuuid.uuid()}"
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
    if not created and cleaned_summary and article.summary_snippet != cleaned_summary:
        article.summary_snippet = cleaned_summary
        article.save(update_fields=["summary_snippet"])
    return article, created


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

    # Render links [text](url)
    text = re.sub(r"\[([^\]]+)\]\(([^\)]+)\)", r'<a href="\2">\1</a>', text)

    # Render inline code `code`
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)

    return text


def _locate_relevant_articles(cleaned_query: str, limit: int = 5) -> List[Article]:
    """Perform a simple full-text style search across the catalogue."""

    if not cleaned_query:
        return []

    filters = (
        Q(title__icontains=cleaned_query)
        | Q(summary_snippet__icontains=cleaned_query)
        | Q(content__icontains=cleaned_query)
    )
    matches = Article.objects.filter(filters).order_by("-updated_at", "title")[:limit]
    return list(matches)


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

    tools = [
        {
            "name": "submit_search_results",
            "description": (
                "Record the final set of search results prepared for a patron. Use polished titles "
                "and two-sentence snippets that summarise the entry."
            ),
            "input_schema": {
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
        }
    ]
    try:
        response = client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=1024,
            temperature=1,
            system=(
                "You staff the EncyclopedAI reference desk. When a patron shares a query, you must "
                "compile reputable encyclopedia search results. Reply by calling the "
                "'submit_search_results' tool exactly once with polished titles and professional "
                "snippets. Each result must also include a disambiguated slug suitable for use in a "
                "URL (lowercase, hyphen-delimited, concise, and unique within the list). Do not "
                "provide any other output. When a suggested entry matches one of the catalogue "
                "records provided in the patron briefing, include its article_id in the tool "
                "payload; otherwise omit the field."
            ),
            messages=[
                {
                    "role": "user",
                    "content": (
                        "A patron would like to consult the archives on the following topic. Provide "
                        "a curated list of relevant entries.\n\n"
                        f"Patron query: {cleaned_query}"
                        + ("\n\n" + "\n".join(briefing_lines) if briefing_lines else "")
                    ),
                }
            ],
            tools=tools,
            tool_choice={"type": "tool", "name": "submit_search_results"},
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Anthropic search request for %s failed", cleaned_query)
        raise RuntimeError("Failed to generate search results.") from exc

    for block in getattr(response, "content", []):
        if getattr(block, "type", "") != "tool_use":
            continue
        if getattr(block, "name", "") != "submit_search_results":
            continue
        tool_payload = getattr(block, "input", {}) or {}
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
            slug_candidate = slugify(raw_slug) if raw_slug else ""
            if not slug_candidate:
                slug_candidate = slugify(title)
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
                article_id = int(raw_identifier) if raw_identifier is not None else None
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
            detail_path = reverse("main:article-detail", kwargs={"slug": entry["slug"]})
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

    raise RuntimeError("Anthropic returned no tool results.")


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
