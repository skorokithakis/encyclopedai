import json
from urllib.parse import urlencode

from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ImproperlyConfigured
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_POST

from . import services
from . import utils
from .models import Article


def index(request):
    query = request.GET.get("q", "").strip()
    error_message = ""
    if query:
        try:
            article, _created = services.get_or_create_article(query)
        except services.ArticleCreationInProgress as exc:
            detail_url = reverse("main:article-detail", kwargs={"slug": exc.slug})
            params = {"fetch": "1"}
            if query and query.lower() != (exc.title or "").lower():
                params["title"] = query
            if params:
                detail_url = f"{detail_url}?{urlencode(params)}"
            return redirect(detail_url)
        except services.DailyArticleLimitExceeded as exc:
            error_message = str(exc)
        except (ImproperlyConfigured, ValueError, RuntimeError):
            pass
        else:
            return redirect("main:article-detail", slug=article.slug)

    latest_articles = Article.objects.order_by("-created_at")[:4]
    random_articles = Article.objects.order_by("?")[:20]
    context = {
        "query": query,
        "csrf_token_value": get_token(request),
        "latest_articles": latest_articles,
        "random_articles": random_articles,
        "error_message": error_message,
    }
    return render(request, "index.html", context)


def article_detail(request, slug: str):
    article = Article.objects.filter(slug=slug).first()
    if article:
        rendered_body = services.render_article_markdown(article.content)
        context = {
            "article": article,
            "rendered_body": rendered_body,
        }
        return render(request, "article_detail.html", context)

    fetch_requested = request.GET.get("fetch") == "1"
    title_hint = (request.GET.get("title") or "").strip()
    snippet_hint = (request.GET.get("snippet") or "").strip()
    display_title = title_hint or services.humanize_slug(slug)
    link_briefings = services.get_incoming_link_briefings(slug)

    if fetch_requested:
        user_agent = request.META.get("HTTP_USER_AGENT", "")
        if not utils.is_whitelisted(user_agent):
            pending_params = request.GET.copy()
            pending_params.pop("fetch", None)
            pending_params["fetch"] = "1"
            fetch_url = f"{request.path}?{pending_params.urlencode()}"
            context = {
                "pending_title": display_title,
                "pending_snippet": snippet_hint,
                "pending_fetch_url": fetch_url,
                "pending_error": _("I'm sorry, the archivists do not work for bots."),
                "pending_link_briefings": link_briefings,
                "pending_notice": "",
            }
            return render(request, "article_pending.html", context, status=403)
        pending_notice = ""
        try:
            article, _created = services.get_or_create_article(
                title_hint or display_title,
                summary_hint=snippet_hint or None,
                slug_hint=slug,
            )
        except services.ArticleCreationInProgress as exc:
            display_title = exc.title or display_title
            error_message = ""
            status_code = 202
            pending_notice = _(
                "Another archivist is already transcribing that entry. The reading room will refresh when the volume is shelved."
            )
        except services.DailyArticleLimitExceeded as exc:
            error_message = str(exc)
            status_code = 503
        except ImproperlyConfigured:
            error_message = _(
                "Access to the archives is briefly suspended. Please try again in a moment."
            )
            status_code = 503
        except ValueError:
            error_message = _("That selection does not appear to be a valid entry.")
            status_code = 400
        except RuntimeError:
            error_message = _(
                "The archives declined to release that manuscript. Please choose another topic."
            )
            status_code = 503
        else:
            if _created:
                try:
                    summary_text = services.generate_article_summary(
                        article.title, article.content
                    )
                except (ImproperlyConfigured, RuntimeError, ValueError):
                    summary_text = ""
                else:
                    summary_text = summary_text.strip()
                if (
                    summary_text
                    and summary_text != (article.summary_snippet or "").strip()
                ):
                    article.summary_snippet = summary_text
                    article.save(update_fields=["summary_snippet"])
            rendered_body = services.render_article_markdown(article.content)
            context = {
                "article": article,
                "rendered_body": rendered_body,
            }
            return render(request, "article_detail.html", context)

        pending_params = request.GET.copy()
        pending_params.pop("fetch", None)
        pending_params["fetch"] = "1"
        fetch_url = f"{request.path}?{pending_params.urlencode()}"
        context = {
            "pending_title": display_title,
            "pending_snippet": snippet_hint,
            "pending_fetch_url": fetch_url,
            "pending_error": error_message,
            "pending_link_briefings": link_briefings,
            "pending_notice": pending_notice,
        }
        return render(request, "article_pending.html", context, status=status_code)

    pending_params = request.GET.copy()
    pending_params["fetch"] = "1"
    fetch_url = f"{request.path}?{pending_params.urlencode()}"
    pending_error = ""
    status_code = 202
    try:
        services.enforce_daily_article_limit()
    except services.DailyArticleLimitExceeded as exc:
        pending_error = str(exc)
        status_code = 503
    context = {
        "pending_title": display_title,
        "pending_snippet": snippet_hint,
        "pending_fetch_url": fetch_url,
        "pending_error": pending_error,
        "pending_link_briefings": link_briefings,
        "pending_notice": "",
    }
    return render(request, "article_pending.html", context, status=status_code)


@require_GET
def search_catalogue(request):
    query = request.GET.get("q", "").strip()
    if not query:
        return JsonResponse(
            {"error": _("Please tell us what you're looking for."), "results": []},
            status=400,
        )

    try:
        results = services.generate_search_results(query)
    except ImproperlyConfigured:
        return JsonResponse(
            {
                "error": _(
                    "The reference desk is calibrating its shelves. Kindly try again shortly."
                ),
                "results": [],
            },
            status=503,
        )
    except (RuntimeError, ValueError):
        return JsonResponse(
            {
                "error": _(
                    "We could not retrieve catalogue entries just now. Please try another topic."
                ),
                "results": [],
            },
            status=503,
        )

    return JsonResponse({"results": results})


@require_POST
def create_article_from_result(request):
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse(
            {"error": _("We could not understand that selection.")},
            status=400,
        )

    title = (payload.get("title") or "").strip()
    snippet = (payload.get("snippet") or "").strip()
    if not title or not snippet:
        return JsonResponse(
            {"error": _("A valid entry requires both a title and a summary snippet.")},
            status=400,
        )

    try:
        article, _created = services.get_or_create_article(
            title,
            summary_hint=snippet,
        )
    except services.ArticleCreationInProgress as exc:
        query_params = {"title": title}
        if snippet:
            query_params["snippet"] = snippet
        pending_url = reverse("main:article-detail", kwargs={"slug": exc.slug})
        if query_params:
            pending_url = f"{pending_url}?{urlencode(query_params)}"
        return JsonResponse(
            {
                "pending": True,
                "url": pending_url,
                "slug": exc.slug,
            },
            status=202,
        )
    except services.DailyArticleLimitExceeded as exc:
        return JsonResponse({"error": str(exc)}, status=503)
    except ImproperlyConfigured:
        return JsonResponse(
            {
                "error": _(
                    "Access to the archives is briefly suspended. Please try again in a moment."
                )
            },
            status=503,
        )
    except ValueError:
        return JsonResponse(
            {"error": _("That selection does not appear to be a valid entry.")},
            status=400,
        )
    except RuntimeError:
        return JsonResponse(
            {
                "error": _(
                    "The archives declined to release that manuscript. Please choose another result."
                )
            },
            status=503,
        )

    detail_url = reverse("main:article-detail", kwargs={"slug": article.slug})
    return JsonResponse({"url": detail_url})


@require_POST
@staff_member_required
def article_delete(request, slug: str):
    """Delete an article and redirect to the home page."""
    article = get_object_or_404(Article, slug=slug)
    article.delete()
    return redirect("main:index")


@require_POST
@staff_member_required
def article_regenerate(request, slug: str):
    """Delete an article and redirect back to its URL to force regeneration."""
    article = get_object_or_404(Article, slug=slug)
    article.delete()
    # Redirect back to the article URL, which will trigger regeneration
    return redirect("main:article-detail", slug=slug)
