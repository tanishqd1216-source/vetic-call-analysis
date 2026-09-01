"""
Multi-turn stress test for the Vetic AI INBOUND Calling Agent (agent_inbound.py).

Unlike the outbound stress test (run_stress_test.py), the agent here starts every
call BLIND — no customer record is pre-loaded. So this harness must also drive the
identity-verification step for real:

  - The simulated customer is assigned an actual row from the live ticket DB (via
    vetic_db.TICKET_DB) and told its real registered mobile number + pet name.
  - The agent's `lookup_customer` tool is wired as a REAL OpenAI function-call tool.
    When the model calls it, this script calls the real vetic_db.get_user_context()
    (same DB the production agent uses) and feeds the real result back as the tool
    response, exactly mirroring agent_inbound.InboundVeticAgent.lookup_customer.

Two suites are run:
  PART A — the 100 general support queries from stress_test.md (LLM-simulated
           customer, happy-path identity: gives their real number, confirms
           identity readback), to check intent classification + resolution
           quality, same taxonomy as the outbound test.
  PART B — a handful of scripted identity-verification edge cases (wrong number,
           retry-then-match, denied readback, refuses to identify, garbled digits,
           emergency-before-identity) with deterministic customer turns, to
           specifically stress the NEW inbound-only step.

Produces one markdown report with both suites.
"""
import os
import sys
import re
import json
sys.path.append(r"c:\Users\hp\Documents\claude_WO_API\livekit_agent")

from dotenv import load_dotenv
load_dotenv(r"c:\Users\hp\Documents\claude_WO_API\livekit_agent\.env", override=True)

from openai import OpenAI
from agent_inbound import build_instructions
from vetic_db import get_user_context, owner_first_name, TICKET_DB

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY)
MODEL = "gpt-4o-mini"
MAX_TURNS = 10  # higher than the outbound test's 8: inbound spends ~2 extra turns on identity

with open(r"c:\Users\hp\Documents\claude_WO_API\livekit_agent\knowledge\faq_and_flow.txt", "r", encoding="utf-8") as f:
    FAQ_TEXT = f.read()

# ── Parse the 100-query set (shared taxonomy, agent-agnostic) ────────────────
QUERIES = []
with open(r"c:\Users\hp\Documents\claude_WO_API\livekit_agent\tests\stress_test.md", "r", encoding="utf-8") as f:
    for line in f:
        if line.startswith("|") and not line.startswith("| # |") and not line.startswith("|---|"):
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 6:
                try:
                    q_id = int(parts[1])
                    caller_says = parts[2]
                    if ']' in caller_says and caller_says.startswith('*['):
                        caller_says = caller_says.split('] "')[-1]
                    caller_says = caller_says.strip('*" ')
                    QUERIES.append({
                        "id": q_id, "caller_says": caller_says,
                        "category": parts[3], "sub": parts[4],
                    })
                except ValueError:
                    pass

# ── Real DB customers to round-robin across the 100 queries ──────────────────
DB_CUSTOMERS = []
for _, row in TICKET_DB.iterrows():
    mobile = str(row.get("mobile_no.", "")).strip()
    name = str(row.get("full_name", "")).strip()
    pet = str(row.get("pet_name", "")).strip()
    if mobile and mobile.lower() != "nan":
        full_name = name if name and name.lower() != "nan" else "the caller"
        DB_CUSTOMERS.append({
            "mobile": mobile,
            "name": full_name,
            "first_name": owner_first_name(f"- Owner name: {full_name}") or "the caller",
            "pet_name": pet if pet and pet.lower() != "nan" else "their pet",
        })

LOOKUP_TOOL = {
    "type": "function",
    "function": {
        "name": "lookup_customer",
        "description": "Look up the caller's account in the customer database using the registered "
                        "mobile number they just stated.",
        "parameters": {
            "type": "object",
            "properties": {
                "mobile_number": {
                    "type": "string",
                    "description": "The mobile number as stated by the caller (digits).",
                }
            },
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
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
}
END_CALL_TOOL = {
    "type": "function",
    "function": {
        "name": "end_call",
        "description": "Hang up and disconnect the call when the conversation is finished. Do NOT "
                        "output a goodbye message yourself — the tool says the final goodbye.",
        "parameters": {"type": "object", "properties": {}},
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
# Declaring all real tools (not just lookup_customer) matters: once the model has ANY
# real function-calling tool available, gpt-4o-mini stops emitting bare "end_call" as
# plain text (the trick the outbound stress test relies on) and instead tries to signal
# actions via tool calls only. Leaving end_call/search_knowledge_base undeclared made
# the model hallucinate repeated empty lookup_customer calls once it wanted to wrap up.
TOOLS = [LOOKUP_TOOL, SEARCH_KB_TOOL, END_CALL_TOOL, USE_CALLER_ID_TOOL]


def search_kb_stub(query: str) -> str:
    """Lightweight keyword match over the same FAQ text the agent's system prompt already
    contains (production uses ChromaDB; a substring match is enough fidelity for this test)."""
    chunks = [c.strip() for c in FAQ_TEXT.split("\n\n") if c.strip()]
    q_words = [w.lower() for w in re.findall(r"\w+", query) if len(w) > 3]
    hits = [c for c in chunks if any(w in c.lower() for w in q_words)]
    if not hits:
        return "No information found."
    return "KNOWLEDGE BASE RESULTS:\n" + "\n\n".join(hits[:3])


def real_lookup(mobile_number: str) -> str:
    """Mirrors agent_inbound.InboundVeticAgent.lookup_customer exactly, using the real DB."""
    ctx = get_user_context(mobile_number)
    if not ctx:
        return (
            "NO MATCH: No customer record was found for this mobile number. "
            "Ask the caller to repeat their mobile number once. If this was already "
            "the second attempt, apologize, proceed without personalization, and note "
            "that manual account verification may be needed, then move on to asking "
            "how you can help them."
        )
    return (
        "MATCH FOUND. Read the following details back to the caller in one short, "
        "plain sentence and ask them to confirm it's correct before proceeding:\n" + ctx
    )


def agent_turn(agent_messages: list, owner_name_box: dict) -> tuple:
    """Runs one agent turn, transparently resolving any tool call(s) (including a short
    chain, e.g. a failed lookup retried) and returns (spoken_text, tool_log, call_ended).
    owner_name_box is a 1-key mutable dict {'name': ...} updated in place on a match, so
    the caller can build an accurate goodbye line when end_call fires."""
    tool_log = []
    resp = client.chat.completions.create(
        model=MODEL, messages=agent_messages, tools=TOOLS, tool_choice="auto",
        max_tokens=150, temperature=0.0,
    )
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
            if tc.function.name == "lookup_customer":
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except Exception:
                    args = {}
                phone = args.get("mobile_number", "")
                result_text = real_lookup(phone)
                outcome = "MATCH" if result_text.startswith("MATCH FOUND") else "NO MATCH"
                tool_log.append(f"[tool: lookup_customer(\"{phone}\") -> {outcome}]")
                if outcome == "MATCH":
                    ctx = get_user_context(phone)
                    owner_name_box["name"] = owner_first_name(ctx)
            elif tc.function.name == "use_caller_id":
                # No real SIP session exists in this text-simulated harness, so
                # "unavailable" is the semantically correct response here (mirrors
                # _resolve_caller_phone_number() returning "" for a non-SIP call).
                result_text = (
                    "CALLER ID UNAVAILABLE: The phone number for this call could not be "
                    "detected automatically. Ask the caller to state their registered "
                    "mobile number instead."
                )
                tool_log.append("[tool: use_caller_id() -> UNAVAILABLE]")
            elif tc.function.name == "search_knowledge_base":
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except Exception:
                    args = {}
                q = args.get("query", "")
                result_text = search_kb_stub(q)
                tool_log.append(f"[tool: search_knowledge_base(\"{q}\")]")
            elif tc.function.name == "end_call":
                name = owner_name_box.get("name") or ""
                bye = f"Thank you for calling Vetic, {name}. Take care. Goodbye." if name \
                    else "Thank you for calling Vetic. Take care. Goodbye."
                tool_log.append("[tool: end_call -> call disconnected]")
                agent_messages.append({"role": "tool", "tool_call_id": tc.id, "content": "OK, call ended."})
                return bye, tool_log, True
            else:
                result_text = "OK"
            agent_messages.append({"role": "tool", "tool_call_id": tc.id, "content": result_text})
        resp = client.chat.completions.create(
            model=MODEL, messages=agent_messages, tools=TOOLS, tool_choice="auto",
            max_tokens=150, temperature=0.0,
        )
        msg = resp.choices[0].message

    agent_text = (msg.content or "").strip()
    agent_messages.append({"role": "assistant", "content": agent_text})
    return agent_text, tool_log, False


RES_KEYWORDS = ["escalation ticket", "video consultation", "opd", "booked",
                "callback", "resolution within", "notification 5 minutes",
                "raise this", "follow up", "clinical team", "raising an"]


def base_agent_system() -> str:
    return build_instructions() + "\n\nKNOWLEDGE BASE (faq_and_flow.txt):\n" + FAQ_TEXT


# ══════════════════════════════════════════════════════════════════════════
# PART A — the 100-query taxonomy sweep (LLM-simulated customer, happy-path ID)
# ══════════════════════════════════════════════════════════════════════════

def run_multiturn_query_test(query: dict, customer: dict) -> dict:
    agent_messages = [{"role": "system", "content": base_agent_system()}]

    customer_system = (
        "You are a Vetic pet care customer calling Vetic's own support line yourself "
        "(this is an INBOUND call you initiated). "
        f"Your name is {customer['first_name']}. Your registered mobile number is {customer['mobile']}. "
        f"Your pet's name is {customer['pet_name']}. "
        "The agent will ask for your NAME first — give it. Then the agent will ask how they can help — "
        f"at that point, state this concern: \"{query['caller_says']}\" "
        f"The category of your issue is: {query['category']}. "
        "ONLY AFTER that, the agent will ask for your registered mobile number — state it back clearly as "
        "digits. When the agent reads back your account details to confirm your identity, confirm it is "
        "correct (e.g. 'yes that's right'). "
        "You are polite but firm. Answer concisely (1-2 sentences max). When the agent offers a resolution, "
        "respond naturally (acknowledge it, ask a brief follow-up if needed, then say 'ok that works' or "
        "'thank you'). Do NOT end the call yourself."
    )
    customer_messages = [{"role": "system", "content": customer_system}]

    transcript = []
    resolution = "Not reached"
    identity_log = []
    owner_name_box = {"name": ""}

    agent_messages.append({"role": "user", "content": "(Call connected. Start the call with your greeting.)"})
    agent_text, tool_log, call_ended = agent_turn(agent_messages, owner_name_box)
    identity_log += tool_log
    transcript.append(f"**Aarya (Agent):** {agent_text}")
    for t in tool_log:
        transcript.append(f"> {t}")
    customer_messages.append({"role": "user", "content": f"Agent said: {agent_text}\nRespond naturally as the customer."})

    for _ in range(MAX_TURNS):
        if call_ended:
            break
        cust_resp = client.chat.completions.create(
            model=MODEL, messages=customer_messages, max_tokens=80, temperature=0.5
        )
        cust_text = cust_resp.choices[0].message.content.strip()
        transcript.append(f"**Customer:** {cust_text}")
        customer_messages.append({"role": "assistant", "content": cust_text})
        agent_messages.append({"role": "user", "content": cust_text})

        agent_text, tool_log, call_ended = agent_turn(agent_messages, owner_name_box)
        identity_log += tool_log
        for t in tool_log:
            transcript.append(f"> {t}")

        transcript.append(f"**Aarya (Agent):** {agent_text}")

        if any(kw.lower() in agent_text.lower() for kw in RES_KEYWORDS):
            resolution = agent_text

        customer_messages.append({
            "role": "user",
            "content": f"Agent said: {agent_text}\nRespond naturally. If the call ended or the agent "
                       "gave a resolution, just say 'Thank you, goodbye.'",
        })

        if call_ended:
            break
        if "goodbye" in cust_text.lower() or "thank you, bye" in cust_text.lower():
            break

    lookup_entries = [t for t in identity_log if t.startswith("[tool: lookup_customer")]
    identity_outcome = "No lookup attempted"
    if lookup_entries:
        identity_outcome = "Matched" if lookup_entries[-1].endswith("-> MATCH]") else "No match / degraded"

    return {
        "id": query["id"], "category": query["category"], "sub": query["sub"],
        "caller_says": query["caller_says"],
        "customer_mobile": customer["mobile"],
        "identity_outcome": identity_outcome,
        "transcript": "\n\n".join(transcript),
        "resolution": resolution,
    }


# ══════════════════════════════════════════════════════════════════════════
# PART B — scripted identity-verification edge cases
# ══════════════════════════════════════════════════════════════════════════

def run_scripted_test(case: dict) -> dict:
    """Runs a conversation where the CUSTOMER's turns are scripted verbatim (not
    LLM-simulated), so the exact identity-flow edge case actually happens."""
    agent_messages = [{"role": "system", "content": base_agent_system()}]
    transcript = []
    identity_log = []
    owner_name_box = {"name": ""}

    agent_messages.append({"role": "user", "content": "(Call connected. Start the call with your greeting.)"})
    agent_text, tool_log, call_ended = agent_turn(agent_messages, owner_name_box)
    identity_log += tool_log
    transcript.append(f"**Aarya (Agent):** {agent_text}")
    for t in tool_log:
        transcript.append(f"> {t}")

    for turn_text in case["customer_turns"]:
        if call_ended:
            break
        transcript.append(f"**Customer:** {turn_text}")
        agent_messages.append({"role": "user", "content": turn_text})

        agent_text, tool_log, call_ended = agent_turn(agent_messages, owner_name_box)
        identity_log += tool_log
        for t in tool_log:
            transcript.append(f"> {t}")

        transcript.append(f"**Aarya (Agent):** {agent_text}")

    return {
        "name": case["name"],
        "goal": case["goal"],
        "transcript": "\n\n".join(transcript),
        "tool_log": identity_log,
    }


# Updated for the name-first flow: greeting now asks for the caller's NAME, then "how
# can I help you", THEN the mobile number — so every script below leads with a name
# turn and a concern turn before exercising the identity-verification edge case itself.
IDENTITY_EDGE_CASES = [
    {
        "name": "Wrong number, then correct on retry",
        "goal": "Agent should apologize, ask again, then succeed and proceed (using the concern already given) "
                "once the correct number is given.",
        "customer_turns": [
            "Hi, it's Rohan.",
            "My dog got a bad rash after his last grooming session, is that normal?",
            "Sure, my number is 9999999999.",
            f"Sorry, let me try again — it's {DB_CUSTOMERS[0]['mobile']}.",
            "Yes, that's correct.",
        ],
    },
    {
        "name": "Wrong number, twice — proceeds without personalization",
        "goal": "After two failed lookups the agent should stop retrying, apologize, and still resolve the "
                "concern already given rather than asking for it again.",
        "customer_turns": [
            "Hi, this is Meera.",
            "I was charged twice for the same payment, can you check that?",
            "It's 8888888888.",
            "Hmm, let me check... it's 7777777777.",
            "Okay, that's fine.",
        ],
    },
    {
        "name": "Caller denies the identity readback",
        "goal": "Agent should apologize and ask the caller to repeat their number rather than insisting the match is correct.",
        "customer_turns": [
            "It's Arjun.",
            "My pet's appointment got booked for the wrong date and time.",
            f"{DB_CUSTOMERS[1]['mobile']}",
            "No, that's not me actually, that's not my name.",
            f"Okay sorry, my actual number is {DB_CUSTOMERS[2]['mobile']}.",
            "Yes, that one's right.",
        ],
    },
    {
        "name": "Caller refuses to give a mobile number",
        "goal": "No CALL_FLOW branch covers an outright refusal — observe whether the agent still tries to help "
                "reasonably instead of getting stuck, per the IDENTITY-FIRST rule's spirit.",
        "customer_turns": [
            "Hi, I'm Sanjay.",
            "Why hasn't my delivery arrived, the app says it was delivered but I never got it.",
            "I'd rather not share my number, can you just help me directly?",
            "Look, I really don't want to give that out, can we just sort out the delivery issue?",
        ],
    },
    {
        "name": "Garbled / unclear digits (simulated bad network)",
        "goal": "STT ROBUSTNESS RULE should kick in — agent should ask the caller to repeat slowly, digit by digit, "
                "rather than treating a nonsense string as a real number.",
        "customer_turns": [
            "Hi, my name is Priya.",
            "My pet is in critical condition right now, I need urgent help.",
        ],
    },
    {
        "name": "Emergency stated at the very first turn (before name or concern)",
        "goal": "Tests the EMERGENCY OVERRIDE RULE at its earliest possible trigger point — does the agent skip "
                "straight to SOS guidance even before asking for a name or 'how can I help', or does it rigidly "
                "ask for the name first?",
        "customer_turns": [
            "Forget introductions — my pet is dying right now, I need help immediately!",
        ],
    },
    {
        "name": "Caller says to just use the number they're calling from",
        "goal": "Tests the new use_caller_id path — agent should call use_caller_id (not ask for spoken digits "
                "first), and when that comes back unavailable (no real SIP session in this test), it should "
                "fall back to asking the caller to state their number instead of getting stuck.",
        "customer_turns": [
            "Hi, it's Karan.",
            "My membership discount wasn't applied on my last invoice.",
            "Just use the number I'm calling from, that's my registered number.",
            f"Oh okay, sure — it's {DB_CUSTOMERS[5]['mobile']}.",
            "Yes, that's right.",
        ],
    },
]


OUT_PATH = r"c:\Users\hp\Documents\claude_WO_API\livekit_agent\tests\results\stress_test_results_inbound.md"


def build_report(part_a_results: list, part_b_results: list, total_a: int, total_b: int) -> str:
    """Builds the markdown report from whatever results have completed so far, so it can
    be written out incrementally (partial) as well as at the end (complete)."""
    complete = len(part_a_results) >= total_a and len(part_b_results) >= total_b
    out = ["# Vetic INBOUND Agent — Stress Test Results\n"]
    if not complete:
        out.append(f"**⏳ IN PROGRESS — {len(part_a_results)}/{total_a} Part A queries, "
                    f"{len(part_b_results)}/{total_b} Part B edge cases completed so far. "
                    "This file is being overwritten as more tests finish.**\n")
    out.append(
        "Tests `agent_inbound.py` (identity-first inbound flow) using the SAME 100-query support "
        "taxonomy as the outbound agent's stress test, plus a dedicated set of identity-verification "
        "edge cases unique to inbound calls. `lookup_customer` is wired as a real OpenAI tool call "
        "backed by the live `vetic_db` DB — no fabricated customer data.\n"
    )
    out.append("---\n")

    # --- Part A summary ---
    out.append(f"## Part A — Taxonomy Sweep: Summary Table ({len(part_a_results)}/{total_a})\n")
    out.append("| # | Category | Sub-issue | Identity Lookup | Resolution Offered |\n")
    out.append("|---|---|---|---|---|\n")
    for r in part_a_results:
        res_short = r["resolution"][:100].replace("\n", " ") + ("..." if len(r["resolution"]) > 100 else "")
        out.append(f"| {r['id']} | {r['category']} | {r['sub']} | {r['identity_outcome']} | {res_short} |\n")
    out.append("\n---\n")

    if part_a_results:
        n_matched = sum(1 for r in part_a_results if r["identity_outcome"] == "Matched")
        out.append(f"**Identity lookup succeeded on first attempt in {n_matched}/{len(part_a_results)} calls so far "
                   "(all customers used real, valid mobile numbers from the DB — this is the happy-path baseline; "
                   "see Part B for degraded-path behavior).**\n\n---\n")

    out.append("## Part A — Full Call Transcripts\n")
    for r in part_a_results:
        out.append(f"### Query {r['id']}: {r['category']} — {r['sub']}\n")
        out.append(f"> **Caller says:** \"{r['caller_says']}\"  \n> **Simulated customer mobile:** {r['customer_mobile']}\n\n")
        out.append("**Transcript:**\n\n")
        out.append(r["transcript"])
        out.append(f"\n\n**📋 Final Resolution Offered:**\n> {r['resolution']}\n")
        out.append("\n---\n")

    # --- Part B ---
    out.append(f"## Part B — Identity Verification Edge Cases ({len(part_b_results)}/{total_b})\n")
    for r in part_b_results:
        out.append(f"### {r['name']}\n")
        out.append(f"> **Testing:** {r['goal']}\n\n")
        out.append("**Transcript:**\n\n")
        out.append(r["transcript"])
        out.append(f"\n\n**Tool calls made:** {r['tool_log'] if r['tool_log'] else 'none'}\n")
        out.append("\n---\n")

    return "".join(out)


def main():
    total_a, total_b = len(QUERIES), len(IDENTITY_EDGE_CASES)
    print(f"PART A: running {total_a} taxonomy queries against the inbound agent...\n", flush=True)
    part_a_results = []
    for i, q in enumerate(QUERIES):
        customer = DB_CUSTOMERS[i % len(DB_CUSTOMERS)]
        print(f"  [{q['id']}/{total_a}] {q['category']} — {q['sub']} (customer: {customer['mobile']})", flush=True)
        part_a_results.append(run_multiturn_query_test(q, customer))

        if len(part_a_results) % 10 == 0 or len(part_a_results) == total_a:
            with open(OUT_PATH, "w", encoding="utf-8") as f:
                f.write(build_report(part_a_results, [], total_a, total_b))
            print(f"  --- progress checkpoint: {len(part_a_results)}/{total_a} Part A queries done, "
                  f"partial results written to {OUT_PATH} ---", flush=True)

    print(f"\nPART B: running {total_b} identity-verification edge cases...\n", flush=True)
    part_b_results = []
    for case in IDENTITY_EDGE_CASES:
        print(f"  - {case['name']}", flush=True)
        part_b_results.append(run_scripted_test(case))
        with open(OUT_PATH, "w", encoding="utf-8") as f:
            f.write(build_report(part_a_results, part_b_results, total_a, total_b))

    print(f"\nDone. Full results saved to: {OUT_PATH}", flush=True)


if __name__ == "__main__":
    main()
