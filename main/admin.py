from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.utils.html import format_html
from djangoql.admin import DjangoQLSearchMixin

from .models import Article
from .models import User

admin.site.register(User, UserAdmin)


@admin.register(Article)
class ArticleAdmin(DjangoQLSearchMixin, admin.ModelAdmin):
    list_display = ["title", "updated_at"]
    readonly_fields = ["slug", "created_at", "updated_at", "view_article_link"]
    search_fields = ["title", "content", "summary_snippet"]
    ordering = ["title"]

    def view_article_link(self, obj):
        """Display a link to view the article on the site."""
        if obj.pk:
            url = obj.get_absolute_url()
            return format_html('<a href="{}" target="_blank">View Article on Site</a>', url)
        return "-"
    view_article_link.short_description = "Article Link"
