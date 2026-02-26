"""
anthropic_limiter.py — Concurrency and rate-limit handling for Anthropic API.

- Limits concurrent AI calls (semaphore) so 4–5 users can use the app without
  overwhelming the API; idle users do not block active ones.
- Retries on 429 with backoff so the 1–2 users doing heavy work are not failed
  unnecessarily.

UI isolation: Streamlit’s st.session_state is per-browser-session. This module
does not store any request- or user-specific data; it only limits how many
Anthropic requests run at once. Each user’s UI and state remain independent.
"""

import logging
import threading
import time
from contextlib import contextmanager

from anthropic import Anthropic

from config import ANTHROPIC_API_KEY

log = logging.getLogger(__name__)

# Allow up to 4 concurrent Anthropic requests (create or stream) across all users.
_ANTHROPIC_SEMAPHORE = threading.Semaphore(4)

_MAX_RETRIES = 3
_BACKOFF_BASE = 2


def _is_rate_limit_error(e):
    s = (str(e) or "").lower()
    return "429" in s or "rate" in s or "overloaded" in s or "capacity" in s


@contextmanager
def stream_slot():
    """
    Hold a concurrency slot for the duration of a streaming request.
    Use around the whole stream block so only up to 4 streams run at once.
    """
    _ANTHROPIC_SEMAPHORE.acquire()
    try:
        yield
    finally:
        _ANTHROPIC_SEMAPHORE.release()


def create_with_retry(**kwargs):
    """
    Call Anthropic messages.create with semaphore and retry on 429.
    Same kwargs as client.messages.create (model, max_tokens, messages, system=..., etc.).
    """
    for attempt in range(_MAX_RETRIES):
        try:
            with _ANTHROPIC_SEMAPHORE:
                client = Anthropic(api_key=ANTHROPIC_API_KEY)
                return client.messages.create(**kwargs)
        except Exception as e:
            if attempt < _MAX_RETRIES - 1 and _is_rate_limit_error(e):
                wait = _BACKOFF_BASE ** attempt
                log.warning("Anthropic rate limit, retry in %ss (attempt %s/%s)", wait, attempt + 1, _MAX_RETRIES)
                time.sleep(wait)
            else:
                raise
    raise RuntimeError("create_with_retry: unexpected exit")
