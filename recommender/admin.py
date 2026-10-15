from django.contrib import admin
from .models import SearchQuery, TrackRecommendation


# Register your models here.

admin.site.register(SearchQuery)
admin.site.register(TrackRecommendation)