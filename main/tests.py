import json
import uuid
from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import services
from .models import Article
from .models import ArticleCreationLock

WHITELISTED_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.1 Safari/605.1.15"
)


@override_settings(
    STATICFILES_STORAGE="django.contrib.staticfiles.storage.StaticFilesStorage",
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    },
)
class IndexViewTests(TestCase):
    def test_index_redirects_to_existing_article(self):
        article = Article.objects.create(
            title="Celestial Harmonies",
            content="## Overview\nA brief entry.",
        )

        response = self.client.get(reverse("main:index"), {"q": "Celestial Harmonies"})

        self.assertRedirects(
            response,
            reverse("main:article-detail", kwargs={"slug": article.slug}),
        )

    @mock.patch(
        "main.services.generate_article_content", return_value="Introductory text."
    )
    def test_index_generates_article_and_redirects(self, generate_article_content):
        response = self.client.get(reverse("main:index"), {"q": "Zepplinology"})

        article = Article.objects.get(title="Zepplinology")
        self.assertEqual(article.content, "Introductory text.")
        self.assertRedirects(
            response,
            reverse("main:article-detail", kwargs={"slug": article.slug}),
        )
        generate_article_content.assert_called_once()
        args, kwargs = generate_article_content.call_args
        self.assertEqual(args, ("Zepplinology",))
        self.assertIsNone(kwargs.get("summary_hint"))
        self.assertEqual(kwargs.get("link_briefings"), [])


@override_settings(
    STATICFILES_STORAGE="django.contrib.staticfiles.storage.StaticFilesStorage",
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    },
)
class ArticleSlugTests(TestCase):
    def test_article_save_preserves_parentheses_in_slug(self):
        article = Article.objects.create(
            title="The Sun (newspaper)",
            content="A venerable publication.",
        )

        self.assertEqual(article.slug, "the-sun-(newspaper)")

    @mock.patch(
        "main.services.generate_article_content",
        return_value="# Heading\n\nBody of the article.",
    )
    def test_get_or_create_article_generates_parenthetical_slug(
        self, mocked_generate_article_content
    ):
        article, created = services.get_or_create_article("The Sun (newspaper)")

        self.assertTrue(created)
        self.assertEqual(article.slug, "the-sun-(newspaper)")
        mocked_generate_article_content.assert_called_once()

    @mock.patch(
        "main.services.generate_article_content",
        return_value="# Heading\n\nBody of the article.",
    )
    def test_get_or_create_article_ignores_parenthesis_stripped_slug_hint(
        self, mocked_generate_article_content
    ):
        article, created = services.get_or_create_article(
            "Flying Dutchman (Legend)", slug_hint="flying-dutchman-legend"
        )

        self.assertTrue(created)
        self.assertEqual(article.slug, "flying-dutchman-(legend)")
        mocked_generate_article_content.assert_called_once()


@override_settings(
    STATICFILES_STORAGE="django.contrib.staticfiles.storage.StaticFilesStorage",
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    },
    DAILY_ARTICLE_LIMIT=1,
)
class ArticleDetailTests(TestCase):
    def test_markdown_renders_as_html(self):
        article = Article.objects.create(
            title="Tea Thermodynamics",
            content="# Tea Thermodynamics\n\n## Section\n*Bullet* point.",
        )

        response = self.client.get(
            reverse("main:article-detail", kwargs={"slug": article.slug})
        )

        self.assertContains(response, "<h2>Section</h2>", html=True)
        self.assertContains(response, "<em>Bullet</em>", html=True)

    def test_detail_allows_parentheses_and_slashes_in_slug(self):
        complex_slug = "archives/topic-(curated)"
        Article.objects.create(
            title="Curated Topic",
            slug=complex_slug,
            content="An especially curated entry.",
        )

        response = self.client.get(
            reverse("main:article-detail", kwargs={"slug": complex_slug})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "especially curated entry.")

    @mock.patch(
        "main.services.generate_article_summary", return_value="A crisp overview."
    )
    @mock.patch(
        "main.services.generate_article_content",
        return_value="# Heading\n\nDetails about the entry.",
    )
    def test_fetch_request_populates_summary(
        self, mock_generate_article_content, mock_generate_article_summary
    ):
        slug = "new-entry"
        response = self.client.get(
            reverse("main:article-detail", kwargs={"slug": slug}),
            {"fetch": "1", "title": "New Entry"},
            HTTP_USER_AGENT=WHITELISTED_USER_AGENT,
        )

        self.assertEqual(response.status_code, 200)
        article = Article.objects.get(slug=slug)
        self.assertEqual(article.summary_snippet, "A crisp overview.")
        mock_generate_article_content.assert_called_once()
        args, kwargs = mock_generate_article_content.call_args
        self.assertEqual(args, ("New Entry",))
        self.assertEqual(kwargs.get("summary_hint"), None)
        self.assertEqual(kwargs.get("link_briefings"), [])
        mock_generate_article_summary.assert_called_once_with(
            "New Entry", "# Heading\n\nDetails about the entry."
        )

    def test_pending_page_surfaces_limit_error_without_waiting(self):
        Article.objects.create(
            title="Limit Placeholder",
            content="Just enough text to count toward the limit.",
        )

        response = self.client.get(
            reverse("main:article-detail", kwargs={"slug": "limit-reached"})
        )

        self.assertEqual(response.status_code, 503)
        self.assertTemplateUsed(response, "article_pending.html")
        self.assertTrue(response.context["pending_error"])
        self.assertNotContains(response, "setTimeout", status_code=503)


@override_settings(
    STATICFILES_STORAGE="django.contrib.staticfiles.storage.StaticFilesStorage",
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    },
)
class ArticleCreationLockingTests(TestCase):
    def test_get_or_create_article_raises_when_lock_active(self):
        ArticleCreationLock.objects.create(
            slug="locked-entry",
            title="Locked Entry",
            token=uuid.uuid4().hex,
            expires_at=timezone.now() + timedelta(minutes=5),
        )

        with self.assertRaises(services.ArticleCreationInProgress):
            services.get_or_create_article("Locked Entry", slug_hint="locked-entry")

    @mock.patch(
        "main.services.generate_article_content",
        return_value="# Heading\n\nBody of the article.",
    )
    def test_get_or_create_article_handles_expired_lock(
        self, mock_generate_article_content
    ):
        ArticleCreationLock.objects.create(
            slug="stale-entry",
            title="Old Title",
            token=uuid.uuid4().hex,
            expires_at=timezone.now() - timedelta(minutes=10),
        )

        article, created = services.get_or_create_article(
            "Fresh Title", slug_hint="stale-entry"
        )

        self.assertTrue(created)
        self.assertEqual(article.slug, "stale-entry")
        mock_generate_article_content.assert_called_once_with(
            "Fresh Title",
            summary_hint=None,
            link_briefings=[],
        )
        self.assertFalse(
            ArticleCreationLock.objects.filter(slug="stale-entry").exists()
        )

    def test_article_detail_fetch_waits_when_lock_active(self):
        ArticleCreationLock.objects.create(
            slug="waiting-entry",
            title="Waiting Entry",
            token=uuid.uuid4().hex,
            expires_at=timezone.now() + timedelta(minutes=5),
        )

        response = self.client.get(
            reverse("main:article-detail", kwargs={"slug": "waiting-entry"}),
            {"fetch": "1"},
            HTTP_USER_AGENT=WHITELISTED_USER_AGENT,
        )

        self.assertEqual(response.status_code, 202)
        self.assertTemplateUsed(response, "article_pending.html")
        self.assertIn(
            "pending_notice",
            response.context,
        )
        self.assertTrue(response.context["pending_notice"])

    def test_create_article_from_result_signals_pending_when_locked(self):
        ArticleCreationLock.objects.create(
            slug="search-entry",
            title="Search Entry",
            token=uuid.uuid4().hex,
            expires_at=timezone.now() + timedelta(minutes=5),
        )

        response = self.client.post(
            reverse("main:article-from-result"),
            data=json.dumps(
                {
                    "title": "Search Entry",
                    "snippet": "A summary from the search desk.",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertTrue(payload.get("pending"))
        self.assertIn("url", payload)
        self.assertIn("search-entry", payload.get("url", ""))
