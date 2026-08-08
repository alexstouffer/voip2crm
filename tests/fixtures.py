"""Synthetic call transcripts + the LLM JSON a good model should return for each.

These let us test the whole pipeline (bucketing, note rendering, conversation
counts, contact flagging) with zero dependence on Ollama or real calls — inject
FIXTURES[name]['llm'] as the model response and assert on the outcome.
"""

FIXTURES = {
    # 1. Committed follow-up with a concrete date.
    "committed_dated": {
        "transcript": (
            "Agent: Hi, this is Alex from Turbo Heat Welding Tools.\n"
            "Caller: Oh hey, good to hear from you.\n"
            "Agent: I wanted to see about getting you set up with the new welder.\n"
            "Caller: Yeah, call me back next Friday and we'll sort the order.\n"
            "Agent: Perfect, Friday it is.\n"
            "Caller: Talk then.\n"
        ),
        "llm": {
            "is_conversation": True,
            "summary": "Discussed the new welder; caller asked for a callback next Friday to place the order.",
            "followup_status": "committed",
            "followup_reason": "Caller: 'call me back next Friday and we'll sort the order'",
            "due_date": None,
            "priority": "MEDIUM",
            "detected_contacts": [],
        },
    },

    # 2. Committed via a requested deliverable, no date.
    "committed_deliverable": {
        "transcript": (
            "Agent: Can I tell you about our pricing?\n"
            "Caller: Sure. Actually, send me a quote for ten units.\n"
            "Agent: Will do.\n"
            "Caller: Appreciate it.\n"
        ),
        "llm": {
            "is_conversation": True,
            "summary": "Caller requested a quote for ten units.",
            "followup_status": "committed",
            "followup_reason": "Caller requested a quote for ten units",
            "due_date": None,
            "priority": "MEDIUM",
            "detected_contacts": [],
        },
    },

    # 3. Soft / non-committal — likely polite maybe. No task.
    "soft": {
        "transcript": (
            "Agent: Would you want to move forward this month?\n"
            "Caller: Hmm, let me think about it and get back to you sometime.\n"
            "Agent: Of course, no rush.\n"
            "Caller: Thanks.\n"
        ),
        "llm": {
            "is_conversation": True,
            "summary": "Caller was non-committal, wants time to think.",
            "followup_status": "soft",
            "followup_reason": "Caller: 'let me think about it ... sometime'",
            "due_date": None,
            "priority": "LOW",
            "detected_contacts": [],
        },
    },

    # 4. Flat not-interested.
    "not_interested": {
        "transcript": (
            "Agent: Is this something you'd consider?\n"
            "Caller: No, we're really not looking for anything like that. Please take us off your list.\n"
            "Agent: Understood, sorry to bother you.\n"
        ),
        "llm": {
            "is_conversation": True,
            "summary": "Caller declined and asked to be removed from the list.",
            "followup_status": "not_interested",
            "followup_reason": "Caller: 'not looking ... take us off your list'",
            "due_date": None,
            "priority": "LOW",
            "detected_contacts": [],
        },
    },

    # 5. Voicemail — should NOT count as a conversation.
    "voicemail": {
        "transcript": (
            "Agent: Hi, you've reached the voicemail of Dave. Please leave a message after the tone.\n"
            "Agent: Hey Dave, it's Alex from Turbo Heat, give me a call back when you get a chance. Thanks.\n"
        ),
        "llm": {
            "is_conversation": False,
            "summary": "Left a voicemail for Dave.",
            "followup_status": "none",
            "followup_reason": "",
            "due_date": None,
            "priority": "LOW",
            "detected_contacts": [],
        },
    },

    # 6. Normal call, no follow-up cue.
    "none": {
        "transcript": (
            "Agent: Just checking the shipment arrived okay.\n"
            "Caller: Yep, all good, thanks for confirming.\n"
            "Agent: Great, have a good one.\n"
            "Caller: You too.\n"
        ),
        "llm": {
            "is_conversation": True,
            "summary": "Confirmed the shipment arrived; no action needed.",
            "followup_status": "none",
            "followup_reason": "",
            "due_date": None,
            "priority": "LOW",
            "detected_contacts": [],
        },
    },

    # 7. Contacts to extract — including a mangled email (must be flagged, not trusted).
    "contacts": {
        "transcript": (
            "Agent: Who's the best person for purchasing?\n"
            "Caller: That'd be Maria Gonzalez, she's our purchasing manager.\n"
            "Agent: Great, how do I reach her?\n"
            "Caller: Email her at maria dot g at acme flooring dot com.\n"
            "Agent: Perfect, and send me a quote meanwhile.\n"
        ),
        "llm": {
            "is_conversation": True,
            "summary": "Identified Maria Gonzalez (purchasing manager) as the buyer; quote requested.",
            "followup_status": "committed",
            "followup_reason": "Agent to send a quote",
            "due_date": None,
            "priority": "MEDIUM",
            "detected_contacts": [
                {"name": "Maria Gonzalez", "title": "Purchasing Manager",
                 "email": "maria.g@acmeflooring.com"},
            ],
        },
    },
}
