from django.shortcuts import render
from django.conf import settings
from google import genai

# Create your views here.

def test(request):
    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    response = client.models.generate_content(model="gemini-3.5-flash-lite",
    contents="You are a pretentious crate-digging musicologist. Recommend 3 songs similar to 'Reckoner' from radiohead and be insufferable about it.")
    context = {"output": response}

    return render(request, "index.html", context)