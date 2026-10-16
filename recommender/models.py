from django.db import models
from urllib.parse import quote
from django.conf import settings

class SearchQuery(models.Model):
    catalog_id = models.BigIntegerField(unique=True)
    title = models.CharField(max_length=255)
    artist = models.CharField(max_length=255)
    artwork_url = models.URLField(max_length=500, blank=True)
    link = models.URLField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    request_count = models.PositiveIntegerField(default=1)

    def __str__(self):
        return f"{self.title}, {self.artist}"


class TrackRecommendation(models.Model):
    search_query = models.ForeignKey(
        SearchQuery, on_delete=models.CASCADE, related_name="recommendations"
    )
    catalog_id = models.BigIntegerField()
    title = models.CharField(max_length=255)
    artist = models.CharField(max_length=255)
    rationale = models.TextField()
    artwork_url = models.URLField(max_length=500, blank=True)
    link = models.URLField(max_length=500, blank=True)
    preview_url = models.URLField(max_length=500, blank=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.title}, {self.artist}"

    @property
    def search_term(self):
        return f"{self.title} {self.artist}"

    @property
    def telegram_link(self):
        if not settings.TELEGRAM_BOT_USERNAME:
            return ""
        return f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}?text={quote(self.search_term, safe='')}"