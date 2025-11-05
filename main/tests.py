from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Article


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
        generate_article_content.assert_called_once_with("Zepplinology")


@override_settings(
    STATICFILES_STORAGE="django.contrib.staticfiles.storage.StaticFilesStorage",
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    },
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
        )

        self.assertEqual(response.status_code, 200)
        article = Article.objects.get(slug=slug)
        self.assertEqual(article.summary_snippet, "A crisp overview.")
        mock_generate_article_content.assert_called_once()
        mock_generate_article_summary.assert_called_once_with(
            "New Entry", "# Heading\n\nDetails about the entry."
        )
