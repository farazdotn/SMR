#!/usr/bin/env python3
"""One-click bootstrapper: creates .venv, installs deps, migrates DB, opens browser, runs server."""
import hashlib
import os
import subprocess
import sys
import time
import urllib.request
import venv
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"
REQ_FILE = ROOT / "requirements.txt"
STAMP_FILE = VENV_DIR / ".installed.sha256"
URL = "http://127.0.0.1:8000"


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def ensure_venv_and_deps():
    py = venv_python()
    if not py.exists():
        print("  [1/3] Setting up local Python environment (first time only)...")
        venv.create(VENV_DIR, with_pip=True)

    req_hash = hashlib.sha256(REQ_FILE.read_bytes()).hexdigest()
    if not STAMP_FILE.exists() or STAMP_FILE.read_text().strip() != req_hash:
        print("  [2/3] Installing required packages (this takes ~15 seconds)...")
        subprocess.check_call(
            [str(py), "-m", "pip", "install", "--upgrade", "pip", "-q"],
            stdout=subprocess.DEVNULL,
        )
        subprocess.check_call(
            [str(py), "-m", "pip", "install", "-r", str(REQ_FILE), "-q"]
        )
        STAMP_FILE.write_text(req_hash)


def wait_until_ready(server_proc) -> bool:
    for _ in range(60):
        if server_proc.poll() is not None:
            return False
        try:
            urllib.request.urlopen(URL, timeout=1)
            return True
        except Exception:
            time.sleep(0.2)
    return False


def main():
    os.chdir(ROOT)
    if os.name == "nt":
        os.system("title Crate Digger")

    clear_screen()
    print("\n  Starting Crate Digger, hang tight...\n")
    ensure_venv_and_deps()
    py = str(venv_python())

    print("  [3/3] Preparing local database...")
    subprocess.check_call([py, "manage.py", "migrate", "--noinput", "-v", "0"])

    # Run Django quietly in the background (--noreload keeps it in a single process)
    server = subprocess.Popen(
        [py, "manage.py", "runserver", "127.0.0.1:8000", "--noreload"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )

    if not wait_until_ready(server):
        err = server.stderr.read() if server.stderr else "Unknown error"
        raise RuntimeError(f"Server couldn't start:\n{err}")

    webbrowser.open(URL)
    clear_screen()
    print("  ========================================================")
    print("    CRATE DIGGER IS RUNNING!")
    print("  ========================================================")
    print()
    print("    The app is open in your web browser.")
    print(f"    If it didn't open automatically, go to: {URL}")
    print()
    print("    Keep this window open while using the site.")
    print("    To stop the server, just press [ENTER] in this window.")
    print("  ========================================================")

    try:
        input("\n  Press [ENTER] at any time to stop the server and close... ")
    finally:
        print("\n  Shutting down Crate Digger... See ya!")
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Shutting down Crate Digger... See ya!")
    except Exception as exc:
        print(f"\n  Something went wrong:\n  {exc}")
        input("\n  Press [ENTER] to close this window...")
        sys.exit(1)