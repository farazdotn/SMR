#!/usr/bin/env python3
import hashlib
import os
import socket
import subprocess
import sys
import time
import venv
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"
REQ_FILE = ROOT / "requirements.txt"
STAMP_FILE = VENV_DIR / ".installed.sha256"
PORT = 8000  


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def pick_port(preferred: int = PORT, attempts: int = 20) -> int:
    """First free port starting at `preferred`.

    On Windows free_port_if_stuck() normally frees the preferred port first.
    Elsewhere we never kill anything: if something else owns the port, use the next one.
    """
    for port in range(preferred, preferred + attempts):
        if not port_in_use(port):
            return port
    raise RuntimeError(f"No free port found between {preferred} and {preferred + attempts - 1}.")


def free_port_if_stuck():
    """If an old server was left running in the background on PORT, kill it."""
    if not port_in_use(PORT):
        return
    if os.name == "nt":
        subprocess.run(
            f'for /f "tokens=5" %a in (\'netstat -aon ^| findstr :{PORT} ^| findstr LISTENING\') do taskkill /F /PID %a',
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.3)


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


def wait_until_ready(server_proc, port: int) -> bool:
    for _ in range(60):
        if server_proc.poll() is not None:
            return False
        if port_in_use(port):
            return True
        time.sleep(0.2)
    return False


def main():
    os.chdir(ROOT)
    if os.name == "nt":
        os.system("title SMR")

    clear_screen()
    print("\n  Starting SMR, hang tight...\n")
    free_port_if_stuck()
    port = pick_port()
    url = f"http://127.0.0.1:{port}"
    ensure_venv_and_deps()
    py = str(venv_python())

    print("  [3/3] Preparing local database...")
    subprocess.check_call([py, "manage.py", "migrate", "--noinput", "-v", "0"])

    server = subprocess.Popen(
        [py, "manage.py", "runserver", f"127.0.0.1:{port}", "--noreload"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if not wait_until_ready(server, port):
        raise RuntimeError(f"Server couldn't start on port {port}.")

    webbrowser.open(url)
    clear_screen()
    print("  ========================================================")
    print("    SMR IS RUNNING!")
    print("  ========================================================")
    print()
    print("    The app is open in your web browser.")
    print(f"    If it didn't open automatically, go to: {url}")
    print()
    print("    Keep this window open while using the site.")
    print("    To stop the server, just press [ENTER] in this window.")
    print("  ========================================================")

    try:
        input("\n  Press [ENTER] at any time to stop the server and close... ")
    finally:
        print("\n  Shutting down SMR... Cya!")
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Shutting down SMR... Cya!")
    except Exception as exc:
        print(f"\n  Something went wrong:\n  {exc}")
        input("\n  Press [ENTER] to close this window...")
        sys.exit(1)