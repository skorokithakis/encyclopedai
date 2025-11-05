"""The main application's URLs."""

from django.urls import path

from . import views

app_name = "main"
urlpatterns = [
    path("", views.index, name="index"),
    path("search/", views.search_catalogue, name="search"),
    path(
        "entries/from-result/",
        views.create_article_from_result,
        name="article-from-result",
    ),
    path("entries/<slug:slug>/", views.article_detail, name="article-detail"),
]
