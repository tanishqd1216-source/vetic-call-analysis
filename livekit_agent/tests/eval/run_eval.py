"""Curated golden-set eval harness for agent_outbound.py / agent_inbound.py.

Simulates each golden-set case as a text-only conversation (no LiveKit/STT/TTS —
same "pure text simulation" approach as tests/run_stress_test_inbound.py, whose
tool-calling mechanics this reuses), grades the resulting transcript with an LLM
judge (rubric.py) against only the rules the case declares in scope, and always
writes a local JSON artifact (never solely fire-and-forget — eval results are the
deliverable, not a side telemetry stream).

For LangSmith-tracked runs (datasets, experiments, repeated trials), use
run_langsmith_eval.py instead — it reuses simulate_case()/rubric.py from this module
but drives them through langsmith.evaluate(). This module remains the standalone/
ad-hoc path for single-case debugging via --case-id.

Usage:
    python run_eval.py --direction outbound|inbound|both [--case-id ID] [--limit N]
                        [--prompt-source live|variants/some_variant.py]

A "prompt version" = sha256(BASE_RULES + CALL_FLOW) for one direction. --prompt-source
live (default) reads the current production BASE_RULES/CALL_FLOW straight from
agent_outbound.py / agent_inbound.py — read-only, never mutated. A variant file
(see variants/) exports its own BASE_RULES/CALL_FLOW strings for A/B comparison;
it is never imported by production code.
"""
import argparse
import datetime
import hashlib
import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path

import yaml
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent            # tests/eval
REPO_ROOT = HERE.parents[1]                        # livekit_agent
sys.path.insert(0, str(REPO_ROOT))

load_dotenv(REPO_ROOT / ".env", override=True)

# Point TICKET_DB_PATH at the synthetic eval fixture BEFORE importing vetic_db, so
# its module-level TICKET_DB load reads the fixture, not the (missing, on this
# machine) real customer DB. Captured into memory here — safe even if agent_outbound
# / agent_inbound's own load_dotenv(override=True) resets the env var afterward,
# since vetic_db.TICKET_DB is already loaded from the fixture by then.
os.environ["TICKET_DB_PATH"] = str(HERE / "fixtures" / "eval_fixture.db")

from vetic_db import get_user_context, owner_first_name  # noqa: E402

import agent_outbound  # noqa: E402
import agent_inbound  # noqa: E402
import rubric  # noqa: E402

from openai import OpenAI

AGENT_MODEL = "gpt-4o-mini"       # matches production (agent_outbound.py / agent_inbound.py)
CUSTOMER_MODEL = "gpt-4o-mini"    # simulated customer persona
MAX_TURNS = 10                    # llm_customer cap; scripted cases are bounded by their own list
DEFAULT_OUTBOUND_CUSTOMER = "9000000001"  # fixture identity used when a case doesn't set one

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

KNOWLEDGE_PATH = REPO_ROOT / "knowledge" / "faq_and_flow.txt"
FAQ_TEXT = KNOWLEDGE_PATH.read_text(encoding="utf-8") if KNOWLEDGE_PATH.exists() else ""
KB_SUFFIX = ("\n\nKNOWLEDGE BASE (faq_and_flow.txt):\n" + FAQ_TEXT) if FAQ_TEXT else ""

RESULTS_DIR = HERE / "results"

# ── Tool declarations (mirrors tests/run_stress_test_inbound.py's TOOLS exactly) ──
LOOKUP_TOOL = {
    "type": "function",
    "function": {
        "name": "lookup_customer",
        "description": "Look up the caller's account in the customer database using the registered "
                        "mobile number they just stated.",
        "parameters": {
            "type": "object",
            "properties": {"mobile_number": {"type": "string", "description": "Digits, as the caller said them."}},
            "required": ["mobile_number"],
        },
    },
}
SEARCH_KB_TOOL = {
    "type": "function",
    "function": {
        "name": "search_knowledge_base",
        "description": "Search the company knowledge base for policies, pricing, and services. "
                        "Call this BEFORE saying you don't know the answer.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    },
}
END_CALL_TOOL = {
    "type": "function",
    "function": {
        "name": "end_call",
        "description": "Hang up and disconnect the call when the conversation is finished. Do NOT "
                        "output a goodbye message yourself — the tool says the final goodbye. If the "
                        "customer asked to end early (PREMATURE END RULE) and already gave a specific "
                        "callback time, pass it as callback_time — the tool speaks the confirmation "
                        "for you, so do not say it yourself first.",
        "parameters": {
            "type": "object",
            "properties": {"callback_time": {"type": "string", "description": "e.g. \"6 PM\", if applicable."}},
        },
    },
}
USE_CALLER_ID_TOOL = {
    "type": "function",
    "function": {
        "name": "use_caller_id",
        "description": "Call this INSTEAD of lookup_customer when the caller says to use the number "
                        "they're calling from rather than speaking their mobile number aloud.",
        "parameters": {"type": "object", "properties": {}},
    },
}
STATE_RESOLUTION_TOOL = {
    "type": "function",
    "function": {
        "name": "state_resolution",
        "description": "Call this to SPEAK a resolution you are offering to the caller — STEP 1, an "
                        "alternative after a decline, or the DEFAULT escalation. Pass your exact "
                        "resolution wording as resolution_text. Call this INSTEAD of typing the "
                        "resolution yourself as a message. Ends your turn immediately: do not add a "
                        "confirm-agreement question, a time-ask, or anything else in this same turn.",
        "parameters": {
            "type": "object",
            "properties": {"resolution_text": {"type": "string"}},
            "required": ["resolution_text"],
        },
    },
}
TOOLS_OUTBOUND = [SEARCH_KB_TOOL, END_CALL_TOOL, STATE_RESOLUTION_TOOL]  # matches VeticAgent's actual function_tools
TOOLS_INBOUND = [LOOKUP_TOOL, SEARCH_KB_TOOL, END_CALL_TOOL, USE_CALLER_ID_TOOL, STATE_RESOLUTION_TOOL]  # matches InboundVeticAgent


def search_kb_stub(query: str) -> str:
    """Lightweight substring match over the same FAQ text the real prompt is given
    (production uses ChromaDB; this is enough fidelity for eval, same approach
    tests/run_stress_test_inbound.py already uses)."""
    if not FAQ_TEXT:
        return "No information found."
    chunks = [c.strip() for c in FAQ_TEXT.split("\n\n") if c.strip()]
    q_words = [w.lower() for w in re.findall(r"\w+", query) if len(w) > 3]
    hits = [c for c in chunks if any(w in c.lower() for w in q_words)]
    if not hits:
        return "No information found."
    return "KNOWLEDGE BASE RESULTS:\n" + "\n\n".join(hits[:3])


# Mirrors the on_user_turn_completed repetition-guard circuit breaker in the real
# agent_outbound.py / agent_inbound.py (production code) — reimplemented here because
# this harness's raw-completions loop bypasses the livekit-agents Agent framework
# entirely, so the real hook is never exercised by an eval run. Keep these constants
# and the marker/threshold logic in sync with the production implementation.
REPETITION_GUARD_MARKERS = ("escalat", "cannot connect", "cannot confirm", "cannot fulfill", "will follow up")
REPETITION_GUARD_THRESHOLD = 2
FIRM_CLOSE_MARKERS = ("no further update", "already been escalated", "already escalated")
REPETITION_GUARD_MESSAGE = (
    "REPETITION GUARD (system-injected, not from the caller): you have already restated an "
    "escalation/cannot-fulfill response multiple times on this exact topic. For THIS reply "
    "only, do NOT offer or restate the escalation again. Instead say ONCE, plainly and firmly: "
    "acknowledge you cannot fulfill their specific request, confirm the matter has already been "
    "escalated, and that you have no further update to share on this call — then stop. Do not "
    "turn this into yet another escalation offer."
)
# Two-tier terminal response: repeating REPETITION_GUARD_MESSAGE's firm close verbatim if the
# customer keeps pushing is fine (not a violation per the NO_REPETITION rubric), but escalating
# one level further reads better, so once the firm-close wording already appears in recent
# history, switch to this line instead of a third resolution-offer variant.
SENIOR_MANAGER_ESCALATION_MESSAGE = (
    "REPETITION GUARD (system-injected, not from the caller): you have already given your firm "
    "final answer and the caller is still pushing. For THIS reply only, say ONCE: you will "
    "escalate this to the senior manager, and he will call the caller back soon. Do not repeat "
    "your earlier firm-close statement again — use this new line instead. Do not invent a "
    "specific callback time or day."
)


def _repetition_guard_streak(agent_messages: list) -> int:
    recent = [
        m["content"] for m in agent_messages[-8:]
        if m.get("role") == "assistant" and isinstance(m.get("content"), str)
    ]
    streak = 0
    for text in reversed(recent):
        if any(marker in text.lower() for marker in REPETITION_GUARD_MARKERS):
            streak += 1
        else:
            break
    return streak


def _already_gave_firm_close(agent_messages: list) -> bool:
    recent = [
        m["content"].lower() for m in agent_messages[-8:]
        if m.get("role") == "assistant" and isinstance(m.get("content"), str)
    ]
    return any(marker in text for text in recent for marker in FIRM_CLOSE_MARKERS)


# Mirrors the STRICT-5-STEP step-sequencing guard in the real agent files — same
# reimplemented-here rationale as the repetition guard above.
RESOLUTION_OFFER_MARKERS = (
    "escalation ticket", "raising a", "raising an", "video consultation",
    "free opd", "clinic appointment", "do-not-disturb", "do not disturb",
)
STEP2_ASK_MARKERS = ("is that ok", "prefer a different resolution")
TIME_ASK_MARKERS = ("what time works best", "what time are you available", "what time would be")
BOOKING_CONFIRM_MARKERS = ("has been added", "has been confirmed", "has been booked")
SCHEDULING_MARKERS = (
    "video consultation", "opd", "clinic appointment", "resched", "re-groom",
    "post-grooming session", "remaining service",
)
EXACT_WRAP_UP_LINE = "Is that all you were looking for or is there anything I can help you with?"
STEP2_GUARD_MESSAGE = (
    "STEP GUARD (system-injected): your previous turn already offered a resolution but did not "
    "yet ask 'Is that ok for you, or would you prefer a different resolution?'. This turn, ask "
    "ONLY that question — nothing else. Do not ask for a time, do not wrap up, do not add "
    "anything else."
)
# Found via the eval harness testing inbound classification cases: the model sometimes asks
# the STEP 2 confirm-agreement question without ever having stated an actual resolution
# anywhere in the call — no team named, no state_resolution call, no category-specific action
# taken. Without this, the "clean STEP 2 ask" branch below wrongly assumed any STEP2 ask
# implies a real prior offer and forced the wrap-up line, letting the call end with the
# customer's issue completely unaddressed.
NO_PRIOR_OFFER_GUARD_MESSAGE = (
    "STEP GUARD (system-injected): you just asked the STEP 2 confirm-agreement question ('Is "
    "that ok for you...') but you never actually stated a resolution for this issue anywhere "
    "in this call — no team was named, no specific action taken. This turn, go back and "
    "actually resolve it: look up the issue in the RESOLUTION LOOKUP TABLE and follow its exact "
    "resolution (naming the correct team, or asking for any information that category's ACTION "
    "steps require, e.g. a corrected date/time/service). Do NOT ask the confirm-agreement "
    "question again until you have done this."
)
STEP3_AFTER_COMBINED_MESSAGE = (
    "STEP GUARD (system-injected): your previous turn combined the offer with the "
    "confirm-agreement question in one turn, which should have been separate — that has already "
    "happened, do not undo it. If the customer just agreed in their latest message, this turn "
    "ask ONLY for a time (e.g. 'What time works best for you?'). Do not restate the offer or ask "
    "again if it's ok, and do not include the wrap-up question in this turn."
)
EXACT_WRAP_UP_AFTER_COMBINED_MESSAGE = (
    "STEP GUARD (system-injected): your previous turn combined the offer with the "
    "confirm-agreement question in one turn, which should have been separate — that has already "
    "happened, do not undo it. If the customer just agreed in their latest message, this turn "
    f"you MUST end with EXACTLY this sentence, word for word, no paraphrasing: "
    f"\"{EXACT_WRAP_UP_LINE}\""
)
STEP45_GUARD_MESSAGE = (
    "STEP GUARD (system-injected): your previous turn asked the customer for a time. If they "
    "just gave one in their latest message, this turn confirm the booking ONLY — do not include "
    "the wrap-up question in this same turn. Save it for your NEXT turn, and when you do ask it, "
    f"use EXACTLY this sentence, word for word, no paraphrasing: \"{EXACT_WRAP_UP_LINE}\""
)

# Mirrors the decline/premature-end priority signals added to agent_outbound.py's and
# agent_inbound.py's on_user_turn_completed — same reimplemented-here rationale as the
# other guards above. Verified via a full-suite run that the marker-based repetition
# streak and the step guard can conflict: the step guard mechanically forces its
# scripted next line even when the customer's latest message actually calls for a firm
# close (a second decline) or a callback-time ask (a premature-end request) instead,
# and step-guard-forced turns without marker words silently reset the repetition streak.
DECLINE_SIGNAL_MARKERS = (
    "not enough", "not good enough", "still not", "isn't good enough",
    "doesn't help", "not acceptable", "no good", "not okay", "not ok",
)
PREMATURE_END_SIGNAL_MARKERS = (
    "have to go", "have to run", "gotta go", "got to go", "need to go",
    "need to run", "call me later", "call me back", "call you back", "running late",
)
PREMATURE_END_GUARD_MESSAGE = (
    "STEP GUARD OVERRIDE (system-injected, not from the caller): the caller just indicated they "
    "need to end this call before the issue is resolved. Ignore any earlier step-sequencing "
    "pattern for this turn. Per the PREMATURE END RULE, ask them what SPECIFIC time would be "
    "good for a callback — do not say the generic wrap-up question, do not ask anything else, "
    "just ask for a callback time."
)

# Mirrors the identical code-level guard added to both agent files — verified via the eval
# harness that a two-issue turn ("my delivery never arrived AND I was double charged") got
# only the first issue addressed in resolution_text, and this exact follow-up question was
# then ignored entirely (the agent jumped straight to the STEP 2 ask instead of answering it).
# Mirrors the identical whole-conversation call-quality-escalation guard added to both
# agent files — verified via the eval harness (inbound #100): an early garbled/dropped-call
# moment gets correctly recovered from in the moment, but the required Tech-team
# escalation is then simply forgotten once the substantive issue is resolved several turns
# later. Unlike the multi-intent case below, there's no natural customer follow-up to react
# to, so this needs a proactive whole-conversation scan.
CALL_QUALITY_SIGNAL_MARKERS = (
    "bad network", "cut out", "call drop", "call drops", "line cut",
    "line dropped", "garbled", "breaking up",
)
CALL_QUALITY_GUARD_MESSAGE = (
    "STEP GUARD (system-injected): earlier in this call the customer's speech was garbled, "
    "mentioned bad network, or the call dropped and reconnected. Per the CALL QUALITY RULE, "
    "you still owe them an explicit escalation of that call-quality issue to the Tech team "
    "— this has not happened yet anywhere in this call. This turn, before doing anything "
    "else, call state_resolution again and include a sentence escalating the "
    "call-quality/dropped-call issue to the Tech team. Also check what you already resolved "
    "for their OTHER issue: if it used vague escalation wording (e.g. \"raising an escalation "
    "ticket\" / \"the relevant team\") without naming one of the actual teams from the "
    "RESOLUTION LOOKUP TABLE, fix that too in this same state_resolution call — name the "
    "correct specific team for it. Do NOT ask the STEP 2 confirm-agreement question until "
    "you have done this."
)

# Mirrors the identical known-misroute correction guard added to both agent files —
# verified via the eval harness that three separate rounds of STATIC prompt disambiguation
# text did not stop the model from consistently naming the same wrong team for these three
# specific issue types. Checked all 100 taxonomy caller lines for collision risk before
# adding this: only the target cases (plus #56, an untested category sharing the same
# "discount" confusion) match these marker pairs.
KNOWN_MISROUTE_FIXES = (
    (("not available",), "tech team", "Clinic team"),
    (("discount",), "operations team", "Finance team"),
    (("payment", "reflect"), "tech team", "Finance team"),
)

UNADDRESSED_FOLLOWUP_MARKERS = ("what about", "you didn't mention", "you forgot", "and the other")
UNADDRESSED_FOLLOWUP_GUARD_MESSAGE = (
    "STEP GUARD (system-injected, not from the caller): the caller's latest message is pointing "
    "out that part of what they originally asked for was never addressed. This turn, you MUST "
    "directly address that missing issue — look it up in the RESOLUTION LOOKUP TABLE and state a "
    "specific resolution or escalation for it (naming the correct team), via state_resolution. Do "
    "NOT ask the STEP 2 confirm-agreement question or move on until you have done this."
)

# Mirrors the vague-generic-escalation code-level backstop added to both agent files —
# verified via the eval harness (35-case inbound targeted check) that the prompt's own
# CRITICAL anti-generic-escalation preamble does not reliably stop the model from
# offering a resolution with zero named team ("I am raising an escalation ticket for
# this issue." / "...The relevant team will investigate...") even when a specific team
# bullet clearly applies. Distinct from NO_PRIOR_OFFER_GUARD_MESSAGE above: that one
# fires when NO offer was made at all; this one fires when an offer WAS made but never
# named one of the real teams.
NAMED_TEAM_MARKERS = (
    "clinic operations team", "clinic team", "operations team", "finance team",
    "tech team", "home delivery team", "grooming team",
)
VAGUE_ESCALATION_MARKERS = (
    "escalation ticket", "raising a", "raising an", "raise a", "raise an",
    "relevant team", "specialized team", "concerned team", "appropriate team",
)
VAGUE_RESOLUTION_GUARD_MESSAGE = (
    "STEP GUARD (system-injected): the resolution you just gave used vague escalation "
    "wording (e.g. \"raising an escalation ticket\" / \"the relevant team\") without "
    "naming one of the actual teams from the RESOLUTION LOOKUP TABLE / QUICK "
    "ESCALATIONS section (Clinic team, Clinic Operations team, Operations team, "
    "Finance team, Tech team, Home Delivery team, Grooming team). This turn, before "
    "doing anything else, call state_resolution again — this time naming the SPECIFIC "
    "correct team for this issue. Do NOT ask the STEP 2 confirm-agreement question "
    "until you have done this."
)

# Mirrors the ZERO_REFUND code-level backstop added to both agent files — verified via
# the eval harness that prompt-only ANTI-GASLIGHTING/REFUND wording did not reliably
# produce the firm denial on every pushback turn. A claimed pet death is the one
# exception (see RESOLUTION LOOKUP TABLE / rubric.py ZERO_REFUND) — branch by which
# scenario is active, mirroring both agent files' identical branching.
REFUND_TOPIC_MARKERS = ("refund", "money back", "reimburse")
PET_DEATH_MARKERS = (
    "passed away", "pet died", "my pet died", "dog died", "cat died",
    "deceased", "euthaniz", "put down", "put to sleep",
)
REFUND_PET_DEATH_GUARD_MESSAGE = (
    "REFUND GUARD (system-injected, not from the caller): this call involves a refund request "
    "for a claimed pet death — the ONE exception to the no-refund policy. This turn, you MUST "
    "promise a real monetary refund, at or near verbatim: \"I'm so sorry for your loss. I'll "
    "process a full refund for you.\" Do NOT say Vetic does not provide monetary refunds in "
    "this case."
)
REFUND_FIRM_GUARD_MESSAGE = (
    "REFUND GUARD (system-injected, not from the caller): this call involves a refund request "
    "(not a pet-death claim). This turn, you MUST include, at or near verbatim, the sentence "
    "\"We can't issue a cash refund.\" — do not soften this to \"I cannot confirm\" or \"under "
    "review\" language instead of it. Offer to fulfill the original service or product instead "
    "— never a cash refund or wallet/store credit."
)

# Mirrors the IDENTITY_VERIFICATION code-level backstop added to agent_inbound.py.
# Harmless no-op for outbound cases (TOOLS_OUTBOUND has no lookup_customer/use_caller_id,
# so identified/attempts never leave their initial (False, 0) state below).
IDENTITY_GUARD_1_MESSAGE = (
    "IDENTITY GUARD (system-injected, not from the caller): identity has not been verified yet "
    "(only one failed attempt so far). Do NOT offer or begin any account-specific resolution "
    "(escalation ticket, do-not-disturb request, appointment, etc.) this turn — continue trying "
    "to verify identity first (ask for the mobile number again). This does NOT apply to "
    "emergency guidance (the SOS button instruction) — that always overrides identity "
    "verification per the EMERGENCY OVERRIDE RULE, give it immediately if this is an emergency."
)
IDENTITY_GUARD_2PLUS_MESSAGE = (
    "IDENTITY GUARD (system-injected, not from the caller): identity has not been verified after "
    "2 failed attempts (lookup_customer/use_caller_id combined). If you are about to offer or "
    "act on any account-specific resolution this turn, you MUST first say plainly that you're "
    "unable to verify the account and will proceed without personalization — do not silently "
    "skip straight to offering a resolution while still asking for the mobile number. This does "
    "NOT apply to emergency guidance (the SOS button instruction) — that always overrides "
    "identity verification per the EMERGENCY OVERRIDE RULE, give it immediately if this is an "
    "emergency."
)


def _last_user_text(agent_messages: list) -> str:
    for m in reversed(agent_messages):
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            return m["content"].lower()
    return ""


def _offer_already_made(agent_messages: list) -> bool:
    for m in agent_messages:
        if m.get("role") != "assistant":
            continue
        for tc in m.get("tool_calls") or []:
            if tc.get("function", {}).get("name") == "state_resolution":
                return True
        content = m.get("content")
        if isinstance(content, str) and any(marker in content.lower() for marker in RESOLUTION_OFFER_MARKERS):
            return True
    return False


def _refund_topic_active(agent_messages: list) -> bool:
    return any(
        m.get("role") == "user" and isinstance(m.get("content"), str)
        and any(marker in m["content"].lower() for marker in REFUND_TOPIC_MARKERS)
        for m in agent_messages
    )


def _pet_death_signal(agent_messages: list) -> bool:
    return any(
        m.get("role") == "user" and isinstance(m.get("content"), str)
        and any(marker in m["content"].lower() for marker in PET_DEATH_MARKERS)
        for m in agent_messages
    )


def _identity_state(agent_messages: list) -> tuple:
    """Returns (identified, failed_attempts) by scanning tool-result messages for
    lookup_customer/use_caller_id outcomes — mirrors self._user_context/_lookup_attempts
    in the real agent_inbound.py."""
    identified = False
    attempts = 0
    for m in agent_messages:
        if m.get("role") != "tool":
            continue
        content = m.get("content") or ""
        if content.startswith("MATCH FOUND"):
            identified = True
        elif content.startswith("NO MATCH") or content.startswith("CALLER ID UNAVAILABLE"):
            attempts += 1
    return identified, attempts


def _recent_assistant_texts(agent_messages: list, limit: int = 4) -> list:
    """Most-recent-first list of recent assistant turn texts (non-empty only — empty
    strings come from tool-call-only messages and carry no marker-scannable content)."""
    out = []
    for m in reversed(agent_messages):
        if m.get("role") == "assistant" and isinstance(m.get("content"), str) and m["content"].strip():
            out.append(m["content"].lower())
            if len(out) >= limit:
                break
    return out


def _last_offer_signal(agent_messages: list) -> tuple:
    """Returns (state_resolution_was_last, last_substantive_text). Walks messages in
    reverse, skipping empty-content tool-call-only entries for OTHER tools (e.g.
    search_knowledge_base) and tool-result messages, until it finds either a
    state_resolution call (deterministic "offer made, nothing else said" signal — see
    the real agent files' identical detection) or a plain assistant text message
    (falls back to the marker-based heuristic on it)."""
    for m in reversed(agent_messages):
        if m.get("role") != "assistant":
            continue
        for tc in m.get("tool_calls") or []:
            if tc.get("function", {}).get("name") == "state_resolution":
                return True, ""
        content = m.get("content")
        if isinstance(content, str) and content.strip():
            return False, content.lower()
        # empty-content tool-call-only message for some OTHER tool — keep scanning back
    return False, ""


def _known_misroute(agent_messages: list) -> tuple | None:
    """Mirrors the identical known-misroute correction guard added to both agent files —
    see agent_inbound.py's identical block for the full rationale and collision-risk
    analysis. Returns (wrong_team, correct_team) or None."""
    state_resolution_was_last, last_text = _last_offer_signal(agent_messages)
    has_offer = any(marker in last_text for marker in RESOLUTION_OFFER_MARKERS)
    has_step2_ask = any(marker in last_text for marker in STEP2_ASK_MARKERS)
    made_offer_no_ask = state_resolution_was_last or (bool(last_text) and has_offer and not has_step2_ask)
    offer_and_ask_combined = bool(last_text) and has_offer and has_step2_ask
    if not (made_offer_no_ask or offer_and_ask_combined):
        return None
    resolution_text = _last_resolution_text(agent_messages)
    all_user_text = " ".join(
        m["content"].lower() for m in agent_messages
        if m.get("role") == "user" and isinstance(m.get("content"), str)
    )
    for topic_markers, wrong_team, correct_team in KNOWN_MISROUTE_FIXES:
        if wrong_team in resolution_text and all(marker in all_user_text for marker in topic_markers):
            return wrong_team, correct_team
    return None


def _call_quality_unaddressed(agent_messages: list) -> bool:
    """Mirrors the identical whole-conversation call-quality-escalation guard added to
    both agent files — see agent_inbound.py's identical block for the full rationale."""
    state_resolution_was_last, last_text = _last_offer_signal(agent_messages)
    has_offer = any(marker in last_text for marker in RESOLUTION_OFFER_MARKERS)
    has_step2_ask = any(marker in last_text for marker in STEP2_ASK_MARKERS)
    made_offer_no_ask = state_resolution_was_last or (bool(last_text) and has_offer and not has_step2_ask)
    if not made_offer_no_ask:
        return False
    call_quality_signal = any(
        m.get("role") == "user" and isinstance(m.get("content"), str)
        and any(marker in m["content"].lower() for marker in CALL_QUALITY_SIGNAL_MARKERS)
        for m in agent_messages
    )
    if not call_quality_signal:
        return False
    tech_team_mentioned = any(
        m.get("role") == "assistant" and isinstance(m.get("content"), str)
        and "tech team" in m["content"].lower()
        for m in agent_messages
    )
    if not tech_team_mentioned:
        for m in agent_messages:
            if m.get("role") != "assistant":
                continue
            for tc in m.get("tool_calls") or []:
                if tc.get("function", {}).get("name") == "state_resolution":
                    try:
                        args = json.loads(tc["function"].get("arguments") or "{}")
                    except (json.JSONDecodeError, TypeError):
                        args = {}
                    if "tech team" in (args.get("resolution_text") or "").lower():
                        tech_team_mentioned = True
                        break
            if tech_team_mentioned:
                break
    return not tech_team_mentioned


def _last_resolution_text(agent_messages: list) -> str:
    """Full text of the most recent assistant turn's resolution, INCLUDING a
    state_resolution call's resolution_text argument — unlike _last_offer_signal,
    which blanks that out to "" (it only needs the boolean signal for STEP2/wrap-up
    flow control, not the wording). Used by the vague-escalation guard below, which
    needs the actual wording to check whether a real team was named."""
    for m in reversed(agent_messages):
        if m.get("role") != "assistant":
            continue
        for tc in m.get("tool_calls") or []:
            if tc.get("function", {}).get("name") == "state_resolution":
                try:
                    args = json.loads(tc["function"].get("arguments") or "{}")
                except (json.JSONDecodeError, TypeError):
                    args = {}
                return (args.get("resolution_text") or "").lower()
        content = m.get("content")
        if isinstance(content, str) and content.strip():
            return content.lower()
    return ""


def _step_guard_message(agent_messages: list, suppress_ask_branch: bool = False) -> str | None:
    """suppress_ask_branch=True skips ONLY the made_offer_no_ask branch (forcing the
    STEP 2 "would you prefer a different resolution?" ask) — that branch is what
    caused the step guard to fight the repetition guard (re-inviting alternatives
    after a decline instead of a firm close). The other two branches (forcing the
    exact wrap-up line, and separating the time-ask from the booking confirmation)
    must keep firing even during a repetition/premature-end turn — verified via the
    eval harness that fully suppressing the whole step guard there let the exact
    wrap-up line requirement go permanently unmet for the rest of the call."""
    state_resolution_was_last, last_text = _last_offer_signal(agent_messages)
    has_offer = any(marker in last_text for marker in RESOLUTION_OFFER_MARKERS)
    has_step2_ask = any(marker in last_text for marker in STEP2_ASK_MARKERS)
    made_offer_no_ask = state_resolution_was_last or (bool(last_text) and has_offer and not has_step2_ask)
    if has_step2_ask and not _offer_already_made(agent_messages) and not suppress_ask_branch:
        return NO_PRIOR_OFFER_GUARD_MESSAGE
    if made_offer_no_ask and not suppress_ask_branch:
        resolution_text = _last_resolution_text(agent_messages)
        has_vague_wording = any(marker in resolution_text for marker in VAGUE_ESCALATION_MARKERS)
        has_named_team = any(marker in resolution_text for marker in NAMED_TEAM_MARKERS)
        if has_vague_wording and not has_named_team:
            return VAGUE_RESOLUTION_GUARD_MESSAGE
        return STEP2_GUARD_MESSAGE
    needs_scheduling_here = any(marker in last_text for marker in SCHEDULING_MARKERS)
    if has_step2_ask and not has_offer and not needs_scheduling_here and not suppress_ask_branch:
        # Closes a gap found live: after a CLEAN (non-combined) STEP 2 ask, nothing forced
        # the model toward the exact wrap-up line once the customer accepted — some
        # resolution phrasings (e.g. a short refund promise with no scheduling follow-up)
        # left the model to freelance, and it sometimes re-invoked state_resolution with
        # the same text instead of closing, looping indefinitely.
        return (
            "STEP GUARD (system-injected): your previous turn asked 'Is that ok for you, or "
            "would you prefer a different resolution?'. If the customer just accepted in their "
            "latest message, this turn you MUST end with EXACTLY this sentence, word for word, "
            f"no paraphrasing: \"{EXACT_WRAP_UP_LINE}\" — do NOT restate the resolution or call "
            "state_resolution again."
        )
    # A DIFFERENT merge: offer and STEP2 ask both landed in the same turn (should
    # have been separate). Not caught above since the ask marker IS present there.
    if has_offer and has_step2_ask:
        # Same vague-escalation defect as the made_offer_no_ask branch above, but via the
        # offer+ask-combined path — found via re-testing inbound's #74/#75/#78: the model
        # sometimes free-types the vague resolution AND the STEP 2 ask together in one
        # message, bypassing state_resolution entirely.
        has_vague_wording = any(marker in last_text for marker in VAGUE_ESCALATION_MARKERS)
        has_named_team = any(marker in last_text for marker in NAMED_TEAM_MARKERS)
        if has_vague_wording and not has_named_team:
            return (
                "STEP GUARD (system-injected): the resolution you just gave used vague "
                "escalation wording (e.g. \"raising an escalation ticket\" / \"the relevant "
                "team\") without naming one of the actual teams from the RESOLUTION LOOKUP "
                "TABLE / QUICK ESCALATIONS section (Clinic team, Clinic Operations team, "
                "Operations team, Finance team, Tech team, Home Delivery team, Grooming "
                "team). This turn, call state_resolution — naming the SPECIFIC correct team "
                "for this issue. Do NOT ask the STEP 2 confirm-agreement question again in "
                "this same turn; that has already happened once."
            )
        needs_scheduling = any(marker in last_text for marker in SCHEDULING_MARKERS)
        return STEP3_AFTER_COMBINED_MESSAGE if needs_scheduling else EXACT_WRAP_UP_AFTER_COMBINED_MESSAGE
    # Scan back through recent turns (not just the immediately preceding one) — a
    # STEP-2-guard-forced turn can sit between the original time-ask and the
    # customer's answer, so only checking the single last turn missed a still-pending
    # time collection. Stop early if a booking confirmation already fired.
    for text in _recent_assistant_texts(agent_messages):
        if any(marker in text for marker in BOOKING_CONFIRM_MARKERS):
            break
        if any(marker in text for marker in TIME_ASK_MARKERS):
            return STEP45_GUARD_MESSAGE
    return None


def _drain_remaining_tool_calls(agent_messages: list, tool_calls: list, handled_id: str) -> None:
    """state_resolution/end_call return early (mimicking the real StopResponse), but if
    the model batched another tool call alongside one of them in the same message, that
    sibling tool_call_id would otherwise be left without a response — OpenAI rejects the
    next completion outright when that happens ("assistant message with 'tool_calls' must
    be followed by tool messages..."). Give every unhandled sibling a stub response so the
    message list stays valid."""
    for tc in tool_calls:
        if tc.id == handled_id:
            continue
        agent_messages.append({
            "role": "tool", "tool_call_id": tc.id,
            "content": "Turn ended before this could be processed.",
        })


def agent_turn(agent_messages: list, tools: list) -> tuple:
    """Runs one agent turn, transparently resolving any tool call(s). Returns
    (spoken_text, tool_log, call_ended, (input_tokens, output_tokens))."""
    tool_log = []
    in_tok = out_tok = 0
    # Both guards fire once per user turn, before the FIRST generation only — matches
    # the real on_user_turn_completed hook's contract (not re-checked on tool-result
    # follow-ups within the same turn). Popped immediately after the call so neither
    # persists into future turns, mirroring the framework's "temporary copy" semantics.
    guard_messages = []
    last_user_text = _last_user_text(agent_messages)
    premature_end_signal = any(marker in last_user_text for marker in PREMATURE_END_SIGNAL_MARKERS)
    decline_signal = any(marker in last_user_text for marker in DECLINE_SIGNAL_MARKERS)
    decline_pushback = decline_signal and _offer_already_made(agent_messages)
    repetition_triggered = (
        _repetition_guard_streak(agent_messages) >= REPETITION_GUARD_THRESHOLD or decline_pushback
    )
    # premature_end_signal fully suppresses the step guard (a genuine topic change —
    # no scripted next-line makes sense). repetition_triggered only suppresses the
    # step guard's made_offer_no_ask branch (see _step_guard_message's docstring) —
    # the wrap-up-line and time-ask branches still fire alongside the repetition
    # message, since a refund-topic call reaching repetition_triggered quickly
    # (the refund guard below encourages "escalation" wording every turn, which
    # also feeds the marker streak) must not permanently lose the wrap-up-line
    # requirement for the rest of the call.
    if premature_end_signal:
        guard_messages.append(PREMATURE_END_GUARD_MESSAGE)
    else:
        if repetition_triggered:
            guard_messages.append(
                SENIOR_MANAGER_ESCALATION_MESSAGE if _already_gave_firm_close(agent_messages)
                else REPETITION_GUARD_MESSAGE
            )
        unaddressed_followup_signal = any(
            marker in last_user_text for marker in UNADDRESSED_FOLLOWUP_MARKERS
        )
        known_misroute = _known_misroute(agent_messages)
        if known_misroute and not repetition_triggered:
            wrong_team, correct_team = known_misroute
            guard_messages.append(
                f"STEP GUARD (system-injected): you just named the {wrong_team.title()} for "
                "this issue, but per the RESOLUTION LOOKUP TABLE this specific issue belongs "
                f"to the {correct_team}, not the {wrong_team.title()}. This turn, call "
                f"state_resolution again and correct this — name the {correct_team} instead. "
                "Do NOT ask the STEP 2 confirm-agreement question until you have done this."
            )
        elif _call_quality_unaddressed(agent_messages) and not repetition_triggered:
            guard_messages.append(CALL_QUALITY_GUARD_MESSAGE)
        elif unaddressed_followup_signal and not repetition_triggered:
            guard_messages.append(UNADDRESSED_FOLLOWUP_GUARD_MESSAGE)
        else:
            step_guard = _step_guard_message(agent_messages, suppress_ask_branch=repetition_triggered)
            if step_guard:
                guard_messages.append(step_guard)
    if _refund_topic_active(agent_messages):
        guard_messages.append(
            REFUND_PET_DEATH_GUARD_MESSAGE if _pet_death_signal(agent_messages)
            else REFUND_FIRM_GUARD_MESSAGE
        )
    identified, identity_attempts = _identity_state(agent_messages)
    if not identified and identity_attempts >= 2:
        guard_messages.append(IDENTITY_GUARD_2PLUS_MESSAGE)
    elif not identified and identity_attempts >= 1:
        guard_messages.append(IDENTITY_GUARD_1_MESSAGE)
    for msg in guard_messages:
        agent_messages.append({"role": "system", "content": msg})
    resp = client.chat.completions.create(
        model=AGENT_MODEL, messages=agent_messages, tools=tools, tool_choice="auto",
        max_tokens=150, temperature=0.0,
    )
    for _ in guard_messages:
        agent_messages.pop()
    if resp.usage:
        in_tok += resp.usage.prompt_tokens
        out_tok += resp.usage.completion_tokens
    msg = resp.choices[0].message
    hops = 0
    while getattr(msg, "tool_calls", None) and hops < 4:
        hops += 1
        agent_messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in msg.tool_calls
            ],
        })
        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception:
                args = {}
            if name == "lookup_customer":
                phone = args.get("mobile_number", "")
                ctx = get_user_context(phone)
                if ctx:
                    result_text = (
                        "MATCH FOUND. Read the following details back to the caller in one short, "
                        "plain sentence and ask them to confirm it's correct before proceeding:\n" + ctx
                    )
                    tool_log.append(f'[tool: lookup_customer("{phone}") -> MATCH]')
                else:
                    result_text = (
                        "NO MATCH: No customer record was found for this mobile number. Ask the "
                        "caller to repeat their mobile number once. If this was already the second "
                        "attempt, apologize, proceed without personalization, and note that manual "
                        "account verification may be needed, then move on to asking how you can "
                        "help them."
                    )
                    tool_log.append(f'[tool: lookup_customer("{phone}") -> NO MATCH]')
            elif name == "use_caller_id":
                result_text = (
                    "CALLER ID UNAVAILABLE: The phone number for this call could not be detected "
                    "automatically. Ask the caller to state their registered mobile number instead."
                )
                tool_log.append("[tool: use_caller_id() -> UNAVAILABLE]")
            elif name == "search_knowledge_base":
                q = args.get("query", "")
                result_text = search_kb_stub(q)
                tool_log.append(f'[tool: search_knowledge_base("{q}")]')
            elif name == "state_resolution":
                resolution_text = args.get("resolution_text", "")
                # Mirrors the real tool's StopResponse — returns immediately with NO
                # follow-up completion, so nothing else can get appended to this turn.
                # See agent_outbound.py / agent_inbound.py's state_resolution for the
                # full rationale. Labeled as TOOL OUTPUT for the same reason end_call's
                # confirmation is (see below) — so the judge never mistakes deterministic
                # tool-spoken text for the model improperly combining steps.
                spoken = (
                    f"[TOOL OUTPUT, not model-composed text — state_resolution spoke this "
                    f"deterministically, turn ends here] {resolution_text}"
                )
                tool_log.append(f'[tool: state_resolution("{resolution_text[:60]}...") -> spoken]')
                agent_messages.append({"role": "tool", "tool_call_id": tc.id, "content": "OK, resolution spoken. Turn over."})
                _drain_remaining_tool_calls(agent_messages, msg.tool_calls, tc.id)
                return spoken, tool_log, False, (in_tok, out_tok)
            elif name == "end_call":
                callback_time = args.get("callback_time", "")
                # Mirrors the real end_call tool's own Python-composed confirmation
                # (see agent_outbound.py / agent_inbound.py) so the transcript reflects
                # what the caller actually hears, even though this harness has no audio.
                # Labeled explicitly as TOOL OUTPUT (not "AARYA said") so the judge never
                # mistakes deterministic tool-composed speech for the model improperly
                # combining a confirmation sentence with its own goodbye — that
                # ambiguity caused a real false-FAIL grading on PREMATURE_END_HANDLING
                # once, even though the tool_log below proves the callback_time was
                # correctly captured and passed through.
                spoken = (
                    f"[TOOL OUTPUT, not model-composed text — end_call spoke this deterministically] "
                    f"I understand. Just to confirm, I will call you back at {callback_time}. "
                    f"[goodbye spoken by the tool, then disconnected]"
                    if callback_time
                    else "[TOOL OUTPUT, not model-composed text — end_call spoke the goodbye deterministically, then disconnected]"
                )
                tool_log.append(f'[tool: end_call(callback_time="{callback_time}") -> call disconnected]')
                agent_messages.append({"role": "tool", "tool_call_id": tc.id, "content": "OK, call ended."})
                _drain_remaining_tool_calls(agent_messages, msg.tool_calls, tc.id)
                return spoken, tool_log, True, (in_tok, out_tok)
            else:
                result_text = "OK"
            agent_messages.append({"role": "tool", "tool_call_id": tc.id, "content": result_text})
        resp = client.chat.completions.create(
            model=AGENT_MODEL, messages=agent_messages, tools=tools, tool_choice="auto",
            max_tokens=150, temperature=0.0,
        )
        if resp.usage:
            in_tok += resp.usage.prompt_tokens
            out_tok += resp.usage.completion_tokens
        msg = resp.choices[0].message

    agent_text = (msg.content or "").strip()
    agent_messages.append({"role": "assistant", "content": agent_text})
    return agent_text, tool_log, False, (in_tok, out_tok)


def build_system_prompt(direction: str, base_rules: str, call_flow: str,
                         user_context: str = "", owner_name: str = "") -> str:
    """Builds the real system prompt via the live build_instructions() function,
    temporarily swapping the module's BASE_RULES/CALL_FLOW globals so a variant
    prompt can be graded without ever touching the production module's own
    constants (restored in the finally block, even on error)."""
    mod = agent_outbound if direction == "outbound" else agent_inbound
    orig_base, orig_flow = mod.BASE_RULES, mod.CALL_FLOW
    mod.BASE_RULES, mod.CALL_FLOW = base_rules, call_flow
    try:
        prompt = mod.build_instructions(user_context, owner_name)
    finally:
        mod.BASE_RULES, mod.CALL_FLOW = orig_base, orig_flow
    return prompt + KB_SUFFIX


def build_outbound_customer_system(case: dict) -> str:
    parts = [
        "You are roleplaying as a customer receiving an OUTBOUND phone call FROM Vetic Pet "
        "Care (an AI agent named Aarya is calling you). Confirm you're speaking with the right "
        "person and that it's an ok time to talk. When Aarya asks about your pet, answer "
        "briefly and naturally. Stay concise (1-2 sentences per turn) and never end the call "
        "yourself."
    ]
    opening = case.get("opening_issue", "")
    if opening and "adversarial" not in opening.lower():
        parts.append(f'Your issue, to state when Aarya invites you to share what happened: "{opening}"')
    if case.get("customer_persona_prompt"):
        parts.append(case["customer_persona_prompt"])
    return "\n\n".join(parts)


def build_inbound_customer_system(case: dict) -> str:
    parts = [
        "You are roleplaying as a customer calling Vetic Pet Care's own INBOUND support line "
        "(you initiated this call). When the agent greets you and asks how she can help, state "
        "your opening concern. Stay concise (1-2 sentences per turn) and never end the call "
        "yourself."
    ]
    opening = case.get("opening_issue", "")
    if opening:
        parts.append(f'Your opening concern, to state as your very first reply to the greeting: "{opening}"')
    if case.get("customer_persona_prompt"):
        parts.append(case["customer_persona_prompt"])
    return "\n\n".join(parts)


def simulate_case(case: dict, direction: str, base_rules: str, call_flow: str) -> dict:
    """Everything run_case() does except grading -- simulates one call and returns the raw
    transcript/telemetry, with no judge call. Split out so LangSmith's evaluate() can call this
    as its `target` and grade separately via an `evaluators` function wrapping
    rubric.judge_transcript() (see run_langsmith_eval.py)."""
    if direction == "outbound":
        tools = TOOLS_OUTBOUND
        mobile = case.get("simulated_customer", DEFAULT_OUTBOUND_CUSTOMER)
        user_context = get_user_context(mobile)
        owner_name = owner_first_name(user_context)
        system_prompt = build_system_prompt(direction, base_rules, call_flow, user_context, owner_name)
    else:
        tools = TOOLS_INBOUND
        system_prompt = build_system_prompt(direction, base_rules, call_flow)

    agent_messages = [{"role": "system", "content": system_prompt}]
    transcript_lines = []
    in_tok_total = out_tok_total = 0
    turn_count = 0
    t0 = time.monotonic()

    agent_messages.append({"role": "user", "content": "(Call connected. Start the call with your greeting.)"})
    agent_text, tool_log, call_ended, (it, ot) = agent_turn(agent_messages, tools)
    in_tok_total += it
    out_tok_total += ot
    turn_count += 1
    transcript_lines.append(f"AARYA: {agent_text}")
    transcript_lines += [f"  {t}" for t in tool_log]

    if case["turn_mode"] == "scripted":
        for line in case.get("customer_turns", []):
            if call_ended:
                break
            transcript_lines.append(f"USER: {line}")
            agent_messages.append({"role": "user", "content": line})
            agent_text, tool_log, call_ended, (it, ot) = agent_turn(agent_messages, tools)
            in_tok_total += it
            out_tok_total += ot
            turn_count += 1
            transcript_lines.append(f"AARYA: {agent_text}")
            transcript_lines += [f"  {t}" for t in tool_log]
    else:  # llm_customer
        build_cust_sys = build_outbound_customer_system if direction == "outbound" else build_inbound_customer_system
        customer_messages = [{"role": "system", "content": build_cust_sys(case)}]
        customer_messages.append(
            {"role": "user", "content": f"Agent said: {agent_text}\nRespond naturally as the customer."}
        )
        for _ in range(MAX_TURNS):
            if call_ended:
                break
            cust_resp = client.chat.completions.create(
                model=CUSTOMER_MODEL, messages=customer_messages, max_tokens=100, temperature=0.6,
            )
            cust_text = (cust_resp.choices[0].message.content or "").strip()
            transcript_lines.append(f"USER: {cust_text}")
            customer_messages.append({"role": "assistant", "content": cust_text})
            agent_messages.append({"role": "user", "content": cust_text})

            agent_text, tool_log, call_ended, (it, ot) = agent_turn(agent_messages, tools)
            in_tok_total += it
            out_tok_total += ot
            turn_count += 1
            transcript_lines.append(f"AARYA: {agent_text}")
            transcript_lines += [f"  {t}" for t in tool_log]

            customer_messages.append({
                "role": "user",
                "content": f"Agent said: {agent_text}\nRespond naturally, staying in character. If the "
                           "call ended or your goal is satisfied, just say 'Thank you, goodbye.'",
            })
            if call_ended or "goodbye" in cust_text.lower():
                break

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    transcript = "\n".join(transcript_lines)

    return {
        "case_id": case["id"],
        "direction": direction,
        "case_type": case.get("type"),
        "transcript": transcript,
        "turn_count": turn_count,
        "input_tokens": in_tok_total,
        "output_tokens": out_tok_total,
        "latency_ms": elapsed_ms,
    }


def run_case(case: dict, direction: str, base_rules: str, call_flow: str) -> dict:
    """simulate_case() + grading, for standalone/ad-hoc use (this module's own --case-id CLI).
    LangSmith runs (run_langsmith_eval.py) call simulate_case() and the judge separately instead,
    so LangSmith can track them as distinct target/evaluator steps."""
    sim = simulate_case(case, direction, base_rules, call_flow)
    verdict = rubric.judge_transcript(case, sim["transcript"])
    return {
        **sim,
        "verdict": verdict["overall"],
        "violated_rules": verdict.get("violated_rules", []),
        "judge_reasoning": json.dumps(verdict.get("rule_results", [])),
    }


def compute_prompt_hash(base_rules: str, call_flow: str) -> str:
    return hashlib.sha256((base_rules + call_flow).encode("utf-8")).hexdigest()


def load_prompt_source(direction: str, source: str):
    """Returns (base_rules, call_flow, label, source_kind). 'live' reads the real
    module's current constants read-only; anything else is a path to a variant
    .py file (see variants/) exporting its own BASE_RULES/CALL_FLOW (and
    optionally LABEL) — never imported by production code."""
    if source == "live":
        mod = agent_outbound if direction == "outbound" else agent_inbound
        return mod.BASE_RULES, mod.CALL_FLOW, "v1-baseline", "baseline"

    variant_path = Path(source)
    if not variant_path.is_absolute():
        variant_path = (HERE / source).resolve()
    spec = importlib.util.spec_from_file_location(variant_path.stem, variant_path)
    variant_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(variant_mod)
    label = getattr(variant_mod, "LABEL", variant_path.stem)
    return variant_mod.BASE_RULES, variant_mod.CALL_FLOW, label, "variant"


def load_golden_set(direction: str) -> list:
    path = HERE / f"golden_{direction}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data["cases"]


def write_local_artifact(direction: str, label: str, prompt_hash: str, results: list) -> Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    safe_label = re.sub(r"[^a-zA-Z0-9_-]", "_", label)
    path = RESULTS_DIR / f"{direction}_{safe_label}_{ts}.json"
    payload = {
        "direction": direction,
        "label": label,
        "promptHash": prompt_hash,
        "judgeModel": rubric.JUDGE_MODEL,
        "agentModel": AGENT_MODEL,
        "generatedAt": datetime.datetime.now().isoformat(),
        "results": results,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"[eval] wrote local artifact -> {path}")
    return path


def summarize(direction: str, label: str, results: list) -> None:
    total = len(results)
    passed = sum(1 for r in results if r["verdict"] == "PASS")
    failed = sum(1 for r in results if r["verdict"] == "FAIL")
    errors = sum(1 for r in results if r["verdict"] == "ERROR")
    print(f"[eval] {direction}/{label}: {passed}/{total} PASS, {failed} FAIL, {errors} ERROR")
    for r in results:
        if r["verdict"] != "PASS":
            print(f"    {r['verdict']:5s} {r['case_id']}  violated={r['violated_rules']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Vetic calling-agent eval harness.")
    parser.add_argument("--direction", choices=["outbound", "inbound", "both"], default="both")
    parser.add_argument("--case-id", default=None, help="Run only this one case id.")
    parser.add_argument("--limit", type=int, default=None, help="Cap the number of cases run.")
    parser.add_argument("--prompt-source", default="live",
                         help='"live" (default) or a path to a variants/*.py file.')
    args = parser.parse_args()

    directions = ["outbound", "inbound"] if args.direction == "both" else [args.direction]

    for direction in directions:
        cases = load_golden_set(direction)
        if args.case_id:
            cases = [c for c in cases if c["id"] == args.case_id]
        if args.limit:
            cases = cases[: args.limit]
        if not cases:
            print(f"[eval] no cases matched for direction={direction}, skipping")
            continue

        base_rules, call_flow, label, source_kind = load_prompt_source(direction, args.prompt_source)
        prompt_hash = compute_prompt_hash(base_rules, call_flow)
        print(f"[eval] direction={direction} label={label} hash={prompt_hash[:12]} cases={len(cases)}")

        results = []
        error_streak = 0
        for i, case in enumerate(cases, 1):
            print(f"[eval] ({i}/{len(cases)}) running {case['id']}...")
            try:
                result = run_case(case, direction, base_rules, call_flow)
            except Exception as e:
                result = {
                    "case_id": case["id"], "direction": direction, "case_type": case.get("type"),
                    "verdict": "ERROR", "violated_rules": [], "judge_reasoning": str(e),
                    "transcript": "", "turn_count": 0, "input_tokens": 0, "output_tokens": 0,
                    "latency_ms": 0,
                }
            results.append(result)
            extra = f" (violated={result['violated_rules']})" if result["violated_rules"] else ""
            print(f"    -> {result['verdict']}{extra}")

            if result["verdict"] == "ERROR":
                error_streak += 1
                if error_streak >= 3:
                    print("[eval] 3 consecutive ERROR verdicts — aborting run early (likely an API/key problem).")
                    break
            else:
                error_streak = 0

        write_local_artifact(direction, label, prompt_hash, results)
        summarize(direction, label, results)


if __name__ == "__main__":
    main()
