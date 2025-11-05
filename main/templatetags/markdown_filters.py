from django import template

from .. import services

register = template.Library()


@register.filter(name="render_markdown")
def render_markdown(value: str) -> str:
    """
    Renders markdown text to HTML using the application's markdown renderer.
    Returns safe HTML that can be displayed in templates.
    """
    return services.render_article_markdown(value)
