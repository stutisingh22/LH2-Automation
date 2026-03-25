"""Minimal iCalendar export for outreach plans."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta


def build_ics(actions: list[dict], title: str = "LH2 Outreach Plan") -> str:
    def _dt(v):
        if not v:
            return None
        try:
            return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except Exception:
            return None

    def _ics_escape(s: str) -> str:
        return (s or "").replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")

    now = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//LH2//Command Center//EN",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:{_ics_escape(title)}",
    ]

    for a in actions or []:
        d = _dt(a.get("scheduled_at"))
        if not d:
            continue
        dtstart = d.astimezone().strftime("%Y%m%dT%H%M%S")
        dtend = (d.astimezone() + timedelta(minutes=30)).strftime("%Y%m%dT%H%M%S")
        ch = a.get("channel") or a.get("action_type") or "Action"
        desc = a.get("description") or ""
        summary = f"{ch}: {desc[:80]}".strip()
        uid = f"{uuid.uuid4()}@lh2"
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{now}",
            f"DTSTART:{dtstart}",
            f"DTEND:{dtend}",
            f"SUMMARY:{_ics_escape(summary)}",
            f"DESCRIPTION:{_ics_escape(desc)}",
            "END:VEVENT",
        ]

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
