#!/usr/bin/env python3
"""
SDN Recovery Simulator — Desktop launcher.

    python3 sdn_app.py

Opens the simulator in a native window (pywebview) or falls back to the
default browser. The HTML/JS app is fully self-contained — no server needed.
"""

import os
import sys
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
HTML = os.path.join(HERE, "app", "index.html")


def run_webview():
    import webview
    webview.create_window(
        "SDN Recovery Simulator",
        HTML,
        width=1280,
        height=800,
        min_size=(900, 600),
        background_color="#0f1117",
    )
    webview.start()


def run_browser():
    url = "file://" + HTML
    print("Opening in browser:", url)
    webbrowser.open(url)
    print("Press Ctrl+C to exit.")
    try:
        import signal
        signal.pause()
    except (ImportError, AttributeError):
        input()


def main():
    if not os.path.exists(HTML):
        sys.exit("app/index.html not found — run from the project root.")
    try:
        run_webview()
    except ImportError:
        print("pywebview not installed — opening in browser instead.")
        print("  For a native window: pip install pywebview")
        run_browser()


if __name__ == "__main__":
    main()
