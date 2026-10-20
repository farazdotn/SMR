from django.contrib import admin
from django.urls import path
from recommender.views import index, search

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", index, name="index"),
    path("search/", search, name="search"),
]
