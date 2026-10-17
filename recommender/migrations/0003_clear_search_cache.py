from django.db import migrations


def clear_search_cache(apps, schema_editor):
    """Empty the recommendation cache before it gets keyed by song.

    Old rows were keyed by iTunes track id, so one song could be stored several
    times (single, album, remaster), and some hold short lists saved before
    the matching fixes. They can't be given a song key reliably, and they are
    only cached Gemini answers, so they are simply regenerated on next search.
    Deleting a SearchQuery cascades to its TrackRecommendation rows.
    """
    SearchQuery = apps.get_model("recommender", "SearchQuery")
    SearchQuery.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("recommender", "0002_trackrecommendation_preview_url"),
    ]

    operations = [
        migrations.RunPython(clear_search_cache, migrations.RunPython.noop),
    ]