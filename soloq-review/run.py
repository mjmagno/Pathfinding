"""Start SoloQ Review and open it in your browser.

    python run.py          # normal mode (needs .env)
    python run.py --demo   # fake data, no API key needed
"""

import os
import sys
import threading
import webbrowser

if "--demo" in sys.argv:
    os.environ["SOLOQ_DEMO"] = "1"

import uvicorn  # noqa: E402

from app.config import load_settings  # noqa: E402


def main():
    settings = load_settings()
    url = f"http://127.0.0.1:{settings.port}"
    print(f"SoloQ Review running at {url}  (Ctrl+C to stop)")
    if "--no-browser" not in sys.argv:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    # 127.0.0.1 only: the app is not reachable from other machines.
    uvicorn.run("app.main:app", host="127.0.0.1", port=settings.port, log_level="warning")


if __name__ == "__main__":
    main()
