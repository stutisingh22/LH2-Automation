"""
SignalHire callback server — run this so SignalHire can POST results to your app (e.g. on localhost via ngrok).

Usage:
  1. Expose your machine: ngrok http 5050  (or use any tunnel)
  2. In keys.env set: SIGNALHIRE_CALLBACK_BASE_URL=https://YOUR_NGROK_URL
  3. Run: python signalhire_callback_server.py
  4. Keep it running while using "Get emails (SignalHire)" in the app.

Uses the same DB as the main app (DATABASE_URL or SQLite from db_ops).
"""

import json
import logging
import os
import socket
from pathlib import Path

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# Load keys.env so config has SIGNALHIRE_* and DATABASE_URL
_env = Path(__file__).resolve().parent / "keys.env"
if _env.exists():
    with open(_env) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip()
                if k and v and k not in os.environ:
                    os.environ[k] = v

from db_ops import update_enrichment_result, update_enrichment_result_by_identifier
from signalhire_enrichment import parse_callback_payload

try:
    from flask import Flask, request
except ImportError:
    log.error("Install flask: pip install flask")
    raise

app = Flask(__name__)


@app.route("/")
def root():
    return (
        "<!DOCTYPE html><html><body style='font-family:sans-serif;padding:2rem;'>"
        "<h1>SignalHire callback server</h1><p>Server is running. SignalHire will POST to <code>/api/signalhire-callback</code>.</p>"
        "<p><a href='/health'>/health</a> → ok</p></body></html>"
    ), 200


@app.route("/api/signalhire-callback", methods=["POST"])
def signalhire_callback():
    body = request.get_data(as_text=True) or "[]"
    request_id_header = (
        request.headers.get("Request-Id")
        or request.headers.get("request-id")
        or request.headers.get("X-Request-Id")
        or ""
    )
    log.info("SignalHire callback received (body length=%s, Request-Id=%s)", len(body), request_id_header or "(none)")
    try:
        try:
            data = json.loads(body)
        except Exception as e:
            log.warning("Callback body not JSON: %s", e)
            data = []
        updates = parse_callback_payload(data, request_id_from_header=request_id_header or None)
        if not updates:
            log.warning("No updates parsed (missing Request-Id header or success result). Raw body: %s", body[:500])
        for request_id, identifier, result_json in updates:
            if request_id:
                update_enrichment_result(request_id, result_json, status="completed")
                log.info("Updated enrichment request_id=%s", request_id)
            elif identifier:
                ok = update_enrichment_result_by_identifier(identifier, result_json, status="completed")
                log.info("Updated enrichment by identifier (no Request-Id header): identifier=%s ok=%s", identifier[:60], ok)
        return "", 200
    except Exception as e:
        log.exception("Callback error: %s", e)
        return "", 500


@app.route("/health", methods=["GET"])
def health():
    return "ok", 200


def _find_available_port(start_port, max_attempts=5):
    """Try binding to start_port, start_port+1, ...; return first available port."""
    for attempt in range(max_attempts):
        port = start_port + attempt
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("", port))
                return port
        except OSError:
            continue
    return start_port


if __name__ == "__main__":
    base_port = int(os.environ.get("PORT", 5050))
    port = _find_available_port(base_port)
    if port != base_port:
        log.warning("Port %s in use, using %s instead — run: ngrok http %s", base_port, port, port)
    else:
        log.info("SignalHire callback server on http://0.0.0.0:%s — run: ngrok http %s", port, port)
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
