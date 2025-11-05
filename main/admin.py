from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from djangoql.admin import DjangoQLSearchMixin

from .models import Article
from .models import User

admin.site.register(User, UserAdmin)


@admin.register(Article)
class ArticleAdmin(DjangoQLSearchMixin, admin.ModelAdmin):
    list_display = ["title", "updated_at"]
    readonly_fields = ["slug", "created_at", "updated_at"]
    search_fields = ["title", "content", "summary_snippet"]
    ordering = ["title"]
