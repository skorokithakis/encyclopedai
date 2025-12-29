from django.db import migrations


def regenerate_outgoing_links(apps, schema_editor):
    """
    Re-extract outgoing_links for all articles using the fixed regex pattern
    that properly handles parentheses in slugs.
    """
    from main.models import Article

    for article in Article.objects.all():
        article.save()


class Migration(migrations.Migration):
    dependencies = [
        ("main", "0007_add_outgoing_links"),
    ]

    operations = [
        migrations.RunPython(regenerate_outgoing_links, migrations.RunPython.noop),
    ]
