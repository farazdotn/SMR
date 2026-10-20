<div align="center">

# 🎧 SMR

### **S**nobbery **M**usic **R**ecommender

Give it your fav songs, and it'll recommend you five other songs based on them<br>
(after psychoanalyzing you and roasting your taste ofc.)

![Django](https://img.shields.io/badge/Django-6-092E20?logo=django&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-003B57?logo=sqlite&logoColor=white)
![HTMX](https://img.shields.io/badge/HTMX-2-3366CC?logo=htmx&logoColor=white)
![Gemini](https://img.shields.io/badge/Gemini_API-8E75B2?logo=googlegemini&logoColor=white)

</div>

---

Built this because I wanted a funny music recommender that also let me hear the tracks immediately and download 'em in my Telegram bot instantly.

## What you get per search

- **5 sonic matches**, each with a 30-second audio preview and instant links to Apple Music, Spotify, YouTube, SoundCloud, and a music-downloading telegram bot.
- **Zero AI hallucinations.** Every recommendation is verified in parallel against the itunes search API.
- **Actually funny.** Lowkey brutal sometimes

## How to run it
If you're in iran you need a good vpn.

### Windows (one-click)

1. Double-click **`start.bat`**.
2. It automatically installs Python (if you don't have it), sets up a local `.venv`, installs the dependencies, preps the SQLite database, and pops open your browser.
3. On first launch, it'll ask you to paste a free Google Gemini API key. (Getting a key takes about 5 seconds, and the setup page walks you through it, dw.)


### macOS / Linux

Just run `start.py`, it'll do everything itself:

```bash
python3 start.py
```

## Under the hood

- **Backend:** Django 6 + SQLite
- **AI layer:** `google-genai` SDK with Pydantic structured outputs and automatic multi-model failover
- **Verification & deduping:** Custom unicode/regex title normalizer + `ThreadPoolExecutor` hitting the iTunes Search API concurrently, caching by canonical song identity so singles, album cuts, and stuff like "Remastered" reissues never waste a second API call
- **Frontend:** HTMX, so searching songs won't annoyingly refresh the page each time

## Screenshots

**SMR itself**
<img width="827" height="895" alt="image" src="https://github.com/user-attachments/assets/30a8846e-2b08-47ce-bcec-b7fa9a7966f4" />


**Setup page**

<img width="1506" height="620" alt="image" src="https://github.com/user-attachments/assets/24d8986a-8f06-49eb-a12a-312b17f3e0cc" />



