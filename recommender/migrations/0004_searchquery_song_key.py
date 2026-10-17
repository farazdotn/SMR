from django.db import migrations, models


class Migration(migrations.Migration):
    # Kept separate from 0003 (which deletes rows) so that data changes and
    # schema changes never share a transaction. PostgreSQL rejects that mix.

    dependencies = [
        ("recommender", "0003_clear_search_cache"),
    ]

    operations = [
        migrations.AlterField(
            model_name="searchquery",
            name="catalog_id",
            field=models.BigIntegerField(),
        ),
        # The table is empty after 0003, so the one-off default is never used
        # for real rows (and could not be: it would clash with the unique flag).
        migrations.AddField(
            model_name="searchquery",
            name="song_key",
            field=models.CharField(default="", max_length=600, unique=True),
            preserve_default=False,
        ),
    ]