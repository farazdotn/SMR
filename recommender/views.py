from django.shortcuts import render
from django.conf import settings
from google import genai
from .gemini_service import get_recommendations
from .catalog import search_candidates, verify_recommendation


# Create your views here.

def test(request):
    context = {
        "candidates": search_candidates("Reckoner Radiohead"),
        "fake": verify_recommendation("Nonexistent Banger 9000", "Definitely Not A Band"),
        "real": verify_recommendation("La Ritournelle", "Sébastien Tellier"),
    }
    return render(request, "index.html", context)