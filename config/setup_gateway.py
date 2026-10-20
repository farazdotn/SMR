import os
import httpx
from django.conf import settings
from django.shortcuts import redirect, render
from dotenv import set_key
from google import genai
from google.genai import errors, types

VPN_MESSAGE = "Connection error. You might need to either turn on or off your VPN."
PING_URL = "https://generativelanguage.googleapis.com/v1beta/models?key=ping"
NETWORK_ERRORS = (httpx.HTTPError, TimeoutError, ConnectionError, OSError)


def _is_location_blocked(message: str) -> bool:
    """Check if Google's error response indicates a regional/geo-IP restriction."""
    text = message.lower()
    return "location" in text or "region" in text or "country" in text


def _check_gemini_reachable() -> str | None:
    """Return a warning message if Gemini is unreachable or geo-blocked, else None."""
    try:
        response = httpx.get(PING_URL, timeout=3.5)
        if _is_location_blocked(response.text) or response.status_code == 403:
            return VPN_MESSAGE
        return None
    except NETWORK_ERRORS:
        return VPN_MESSAGE


def _verify_api_key(api_key: str) -> None:
    """Confirm Google accepts the API key before saving it (uses zero quota)."""
    try:
        # Fast pre-check so an offline connection fails in 3s instead of
        # getting stuck in the Google SDK's internal retry loop.
        httpx.get(PING_URL, timeout=3)
        client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=5_000),
        )
        client.models.get(model=settings.GEMINI_MODEL)
    except errors.APIError as exc:
        if _is_location_blocked(str(exc)):
            raise ValueError(VPN_MESSAGE) from exc
        if exc.code in (400, 401, 403):
            raise ValueError(
                "Google rejected that API key. Make sure you copied the whole thing."
            ) from exc
        raise ValueError(VPN_MESSAGE) from exc
    except NETWORK_ERRORS as exc:
        raise ValueError(VPN_MESSAGE) from exc


def _save_api_key(api_key: str) -> None:
    """Write the API key to the local .env file and load it into the current process."""
    env_path = settings.BASE_DIR / ".env"
    env_path.touch(exist_ok=True)
    set_key(str(env_path), "GEMINI_API_KEY", api_key)
    os.environ["GEMINI_API_KEY"] = api_key
    settings.GEMINI_API_KEY = api_key


class SetupGatewayMiddleware:
    """Blocks access with a one-time setup screen until GEMINI_API_KEY is configured."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        # We use process_view instead of __call__ so Django's CsrfViewMiddleware
        # validates the POST token before this code runs.
        if settings.GEMINI_API_KEY:
            return None

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
                    _save_api_key(api_key)
                    return redirect(request.path)
        else:
            vpn_warning = _check_gemini_reachable()

        context = {"error": error, "vpn_warning": vpn_warning}
        if request.headers.get("HX-Request"):
            response = render(request, "setup.html", context)
            response["HX-Refresh"] = "true"
            return response

        return render(request, "setup.html", context)