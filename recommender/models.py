from django.db import models

# Create your models here.

class SearchQuery(models.Model):
    query_key = models.CharField(max_length=255, unique=True)
    raw_input = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    request_count = models.PositiveIntegerField(default=1)

    def __str__(self):
        return self.raw_input
    
class TrackRecommendation(models.Model):
    search_query = models.ForeignKey(SearchQuery, on_delete=models.CASCADE, related_name="recommendations")
    title = models.CharField(max_length=255)
    artist = models.CharField(max_length=255)
    rationale = models.TextField(blank=True)

    def __str__(self):
        return f"{self.title}, {self.artist}"
