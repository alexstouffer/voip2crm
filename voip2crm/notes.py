"""Render a CRM note (title + markdown body) from a CallRecord.

Shared by the CRM adapters so the exact text is testable without a live CRM.
The body surfaces, in order:
  - conversation vs voicemail + the running count
  - follow-up classification (committed / soft / not_interested / none)
  - detected contacts flagged for verification (names/titles/emails — never
    auto-committed to the contact's real fields)
  - summary + full transcript
"""
from __future__ import annotations

from .models import CallRecord

_FOLLOWUP_LABEL = {
    "committed": "COMMITTED follow-up",
    "soft": "Soft / non-committal (your call)",
    "not_interested": "NOT INTERESTED",
    "none": "No follow-up",
}


def render_note_title(rec: CallRecord) -> str:
    kind = "Voicemail" if not rec.is_conversation else "Call"
    return f"{kind} — {rec.display_name()} — {_date(rec)}"


def render_note_body(rec: CallRecord) -> str:
    lines: list[str] = []

    # Conversation vs voicemail + counts.
    if rec.is_conversation:
        head = f"**Conversation #{rec.conversation_number}**"
        if rec.voicemail_count:
            head += f" (plus {rec.voicemail_count} voicemail{'s' if rec.voicemail_count != 1 else ''} to this number)"
    else:
        head = f"**Voicemail — no conversation** (attempt #{rec.voicemail_count})"
    if rec.classification_uncertain:
        head += "  \n_⚠ conversation/voicemail classification uncertain — check_"
    lines.append(head)

    # Follow-up classification.
    label = _FOLLOWUP_LABEL.get(rec.followup_status, rec.followup_status)
    fu = f"**Follow-up:** {label}"
    if rec.followup_status == "committed" and rec.followup_due:
        fu += f" — due {rec.followup_due.strftime('%Y-%m-%d %H:%M')}"
    lines.append(fu)
    if rec.followup_reason:
        lines.append(f"> {rec.followup_reason}")

    # Detected contacts — flagged, not committed.
    block = _contacts_block(rec.detected_contacts)
    if block:
        lines.append(block)

    # Summary + transcript.
    lines.append(f"**Summary:** {rec.summary}")
    lines.append(f"**Caller:** {rec.display_name()}  \n**Received:** {_date(rec)}")
    if rec.recording_ref:
        lines.append(f"**Recording:** {rec.recording_ref}")
    lines.append("\n---\n")
    lines.append(rec.best_transcript())

    return "\n\n".join(lines)


def _contacts_block(contacts: list) -> str:
    if not contacts:
        return ""
    rows = []
    for c in contacts:
        name = (c.get("name") or "").strip()
        title = (c.get("title") or "").strip()
        email = (c.get("email") or "").strip()
        parts = []
        if name:
            parts.append(f"Name: {name}")
        if title:
            parts.append(f"Title: {title}")
        if email:
            parts.append(f"Email: {email} (possible)")
        if parts:
            rows.append("- " + " · ".join(parts))
    if not rows:
        return ""
    return "**⚠ Detected — verify before saving:**\n" + "\n".join(rows)


def _date(rec: CallRecord) -> str:
    return rec.received_at.strftime("%Y-%m-%d %H:%M") if rec.received_at else "unknown"
