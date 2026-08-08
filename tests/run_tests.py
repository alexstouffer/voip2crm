"""Test suite for the LLM-extraction pipeline. Runs with no Ollama and no
network by injecting the fixture LLM responses.

Run directly:   python tests/run_tests.py
Or with pytest:  pytest tests/
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.fixtures import FIXTURES
from voip2crm.extract import Extractor
from voip2crm.models import CallRecord
from voip2crm.notes import render_note_body, render_note_title
from voip2crm.state import State


def _extractor_for(name):
    """An Extractor whose LLM client always returns this fixture's response."""
    resp = FIXTURES[name]["llm"]
    return Extractor({"use_llm": True}, llm_client=lambda system, user: resp)


def _record(name):
    rec = CallRecord(message_id=f"AC-{name}", caller_phone="+15626653629")
    rec.transcript = FIXTURES[name]["transcript"]
    return rec


# --- follow-up bucketing ---------------------------------------------------

def test_committed_creates_task_signal():
    rec = _record("committed_dated")
    _extractor_for("committed_dated").enrich(rec)
    assert rec.followup_status == "committed"
    assert rec.followup_needed is True          # pipeline creates a task only on this
    assert rec.is_conversation is True


def test_committed_deliverable_no_date():
    rec = _record("committed_deliverable")
    _extractor_for("committed_deliverable").enrich(rec)
    assert rec.followup_status == "committed"
    assert rec.followup_needed is True


def test_soft_does_not_create_task():
    rec = _record("soft")
    _extractor_for("soft").enrich(rec)
    assert rec.followup_status == "soft"
    assert rec.followup_needed is False         # surfaced in note, NOT a task
    assert rec.followup_due is None


def test_not_interested():
    rec = _record("not_interested")
    _extractor_for("not_interested").enrich(rec)
    assert rec.followup_status == "not_interested"
    assert rec.followup_needed is False


def test_none_no_followup():
    rec = _record("none")
    _extractor_for("none").enrich(rec)
    assert rec.followup_status == "none"
    assert rec.followup_needed is False


# --- conversation vs voicemail ---------------------------------------------

def test_voicemail_not_a_conversation():
    rec = _record("voicemail")
    _extractor_for("voicemail").enrich(rec)
    assert rec.is_conversation is False
    assert rec.followup_needed is False


def test_conversation_detected():
    rec = _record("committed_dated")
    _extractor_for("committed_dated").enrich(rec)
    assert rec.is_conversation is True


def test_classification_conflict_flagged():
    # LLM says voicemail but transcript clearly has two speakers -> uncertain flag.
    rec = _record("committed_dated")
    ext = Extractor({"use_llm": True}, llm_client=lambda s, u: {
        "is_conversation": False, "followup_status": "none"})
    ext.enrich(rec)
    assert rec.classification_uncertain is True


# --- contact extraction (flagged, not committed) ---------------------------

def test_contacts_flagged():
    rec = _record("contacts")
    _extractor_for("contacts").enrich(rec)
    assert len(rec.detected_contacts) == 1
    c = rec.detected_contacts[0]
    assert c["name"] == "Maria Gonzalez"
    assert c["title"] == "Purchasing Manager"
    # Email present but rendered as "(possible)" so it is never auto-trusted.
    body = render_note_body(rec)
    assert "verify before saving" in body.lower()
    assert "(possible)" in body


# --- note rendering --------------------------------------------------------

def test_note_shows_conversation_count():
    rec = _record("none")
    _extractor_for("none").enrich(rec)
    rec.conversation_number = 3
    rec.voicemail_count = 2
    body = render_note_body(rec)
    assert "Conversation #3" in body
    assert "2 voicemails" in body


def test_voicemail_note_title_and_body():
    rec = _record("voicemail")
    _extractor_for("voicemail").enrich(rec)
    rec.voicemail_count = 1
    assert render_note_title(rec).startswith("Voicemail —")
    assert "no conversation" in render_note_body(rec).lower()


def test_committed_note_shows_due_when_set():
    from datetime import datetime
    rec = _record("committed_dated")
    _extractor_for("committed_dated").enrich(rec)
    rec.followup_due = datetime(2026, 7, 24, 9, 0)
    body = render_note_body(rec)
    assert "COMMITTED" in body
    assert "2026-07-24" in body


# --- conversation counting (State) -----------------------------------------

def test_call_counts_conversations_only():
    db = os.path.join(tempfile.mkdtemp(), "s.sqlite")
    st = State(db)
    phone = "+15626653629"
    assert st.call_counts(phone) == (0, 0)
    st.log_call(phone, "AC1", True)     # conversation
    st.log_call(phone, "AC2", False)    # voicemail
    st.log_call(phone, "AC3", True)     # conversation
    conv, vm = st.call_counts(phone)
    assert conv == 2 and vm == 1
    # Idempotent: same call_id doesn't double-count.
    st.log_call(phone, "AC3", True)
    assert st.call_counts(phone) == (2, 1)
    # Number matching ignores formatting.
    assert st.call_counts("(562) 665-3629") == (2, 1)
    st.close()


def test_fallback_when_llm_unavailable():
    # No client + use_llm True but provider will fail -> keyword fallback holds.
    rec = _record("committed_dated")
    def boom(system, user):
        raise RuntimeError("ollama down")
    ext = Extractor({"use_llm": True, "followup_keywords": ["call me back"]},
                    llm_client=boom)
    ext.enrich(rec)
    # Rule-based caught the keyword, so the net didn't drop the follow-up.
    assert rec.followup_status == "committed"
    assert "llm unavailable" in rec.followup_reason.lower()


# --- runner ----------------------------------------------------------------

def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run())
