from django.contrib.postgres.indexes import GinIndex
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("main", "0004_articlecreationlock"),
    ]

    operations = [
        migrations.RunSQL(
            "CREATE EXTENSION IF NOT EXISTS pg_trgm",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.AddIndex(
            model_name="article",
            index=GinIndex(
                fields=["content"],
                name="article_content_trgm",
                opclasses=["gin_trgm_ops"],
            ),
        ),
    ]
