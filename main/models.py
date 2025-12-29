import re

import shortuuid
from django.contrib.auth.models import AbstractUser
from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.urls import reverse

from .slugs import encyclopedai_slugify

# Pattern to extract slugs from internal markdown links like [text](/entries/slug).
_OUTGOING_LINK_PATTERN = re.compile(r"\[[^\]]+\]\(/entries/([^/)#?]+)")


def extract_outgoing_links(content: str) -> list[str]:
    """Extract unique slugs from markdown links pointing to /entries/."""
    if not content:
        return []
    return list(
        set(match.group(1) for match in _OUTGOING_LINK_PATTERN.finditer(content))
    )


class User(AbstractUser):
    pass


class Article(models.Model):
    id: int
    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True, editable=False)
    content = models.TextField()
    summary_snippet = models.TextField(blank=True, default="")
    # Denormalized field storing slugs of articles this article links to, enabling
    # fast reverse lookups for "what links here" queries via GIN index.
    outgoing_links = ArrayField(
        models.CharField(max_length=255),
        default=list,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["title"]
        indexes = [
            GinIndex(
                fields=["content"],
                name="article_content_trgm",
                opclasses=["gin_trgm_ops"],
            ),
            GinIndex(
                fields=["title"],
                name="article_title_trgm",
                opclasses=["gin_trgm_ops"],
            ),
            GinIndex(
                fields=["summary_snippet"],
                name="article_summary_trgm",
                opclasses=["gin_trgm_ops"],
            ),
            GinIndex(
                fields=["outgoing_links"],
                name="article_outgoing_links_gin",
            ),
        ]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        """Return the URL to view this article."""
        return reverse("main:article-detail", kwargs={"slug": self.slug})

    @property
    def content_preview(self) -> str:
        """
        Returns the first few lines of the article content for preview purposes.
        Skips any initial heading and preserves markdown formatting in the body text.
        Truncates to approximately 300 characters, trying to end at paragraph or
        sentence boundaries.
        """
        import re

        if not self.content:
            return ""

        from main.services import strip_leading_heading

        text = strip_leading_heading(self.content)

        # Strip markdown links to prevent nested anchor tags when rendered inside
        # a link element. This converts [text](url) to just text.
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)

        if not text:
            return ""

        # If remaining content is short enough, return it as-is.
        if len(text) <= 300:
            return text

        # Take first 300 characters as a starting point.
        preview = text[:300]

        # Try to end at a paragraph boundary (double newline).
        paragraph_break = preview.rfind("\n\n")
        if paragraph_break > 100:
            return preview[:paragraph_break].strip()

        # Try to end at a sentence boundary.
        last_period = preview.rfind(".")
        last_question = preview.rfind("?")
        last_exclamation = preview.rfind("!")
        sentence_end = max(last_period, last_question, last_exclamation)

        if sentence_end > 100:
            return preview[: sentence_end + 1].strip()

        # Try to end at a newline.
        last_newline = preview.rfind("\n")
        if last_newline > 100:
            return preview[:last_newline].strip()

        # Otherwise, end at a word boundary.
        last_space = preview.rfind(" ")
        if last_space > 0:
            return preview[:last_space].strip() + "..."

        return preview.strip() + "..."

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = (
                encyclopedai_slugify(self.title) or f"article-{shortuuid.uuid()}"
            )
            slug_candidate = base_slug
            index = 1
            while (
                Article.objects.filter(slug=slug_candidate).exclude(pk=self.pk).exists()
            ):
                index += 1
                slug_candidate = f"{base_slug}-{index}"
            self.slug = slug_candidate
        self.outgoing_links = extract_outgoing_links(self.content)
        super().save(*args, **kwargs)


class ArticleCreationLock(models.Model):
    slug = models.SlugField(max_length=255, unique=True)
    title = models.CharField(max_length=255)
    token = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["slug"]

    def __str__(self) -> str:
        return f"Lock for {self.slug}"
