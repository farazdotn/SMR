import os
import httpx
from django.conf import settings
from django.shortcuts import redirect, render
from dotenv import set_key
from google import genai
from google.genai import errors, types

VPN_MESSAGE = "Connection error — you might need to either turn on or off your VPN."
PING_URL = "https://generativelanguage.googleapis.com/v1beta/models?key=ping"


def _check_gemini_reachable() -> str | None:
    """Return a gentle warning if Gemini is unreachable or geo-blocked, else None."""
    try:
        resp = httpx.get(PING_URL, timeout=3.5)
        if "location" in resp.text.lower() or resp.status_code == 403:
            return VPN_MESSAGE
        return None
    except (httpx.HTTPError, TimeoutError, ConnectionError):
        return VPN_MESSAGE


def _verify_api_key(api_key: str) -> None:
    """Confirm Google accepts the API key before saving it (uses zero quota)."""
    try:
        httpx.get(PING_URL, timeout=3)
        client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=5_000),
        )
        client.models.get(model=settings.GEMINI_MODEL)
    except errors.APIError as exc:
        msg = str(exc).lower()
        if "location" in msg or "region" in msg or "country" in msg:
            raise ValueError(VPN_MESSAGE) from exc
        if exc.code in (400, 401, 403):
            raise ValueError(
                "Google rejected that API key. Make sure you copied the whole thing."
            ) from exc
        raise ValueError(VPN_MESSAGE) from exc
    except (httpx.HTTPError, TimeoutError, ConnectionError, OSError) as exc:
        raise ValueError(VPN_MESSAGE) from exc


class SetupGatewayMiddleware:
    """Blocks access with a one-time setup screen until GEMINI_API_KEY is configured."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        if settings.GEMINI_API_KEY:
            return None  # Key exists; let the app run normally

        error = None
        vpn_warning = None

        if request.method == "POST":
            api_key = request.POST.get("api_key", "").strip()
            if not api_key:
                error = "Paste an API key first."
            else:
                try:
                    _verify_api_key(api_key)
                except ValueError as exc:
                    error = str(exc)
                else:
                    env_path = settings.BASE_DIR / ".env"
                    env_path.touch(exist_ok=True)
                    set_key(str(env_path), "GEMINI_API_KEY", api_key)
                    os.environ["GEMINI_API_KEY"] = api_key
                    settings.GEMINI_API_KEY = api_key
                    return redirect(request.path)
        else:
            vpn_warning = _check_gemini_reachable()

        context = {"error": error, "vpn_warning": vpn_warning}
        if request.headers.get("HX-Request"):
            response = render(request, "setup.html", context)
            response["HX-Refresh"] = "true"
            return response

        return render(request, "setup.html", context)