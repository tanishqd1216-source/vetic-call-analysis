"""
Multi-turn stress test for the Vetic AI Calling Agent.
Simulates a realistic call conversation for each query and produces
a full transcript + extracted resolution.
"""
import os
import sys
import re
sys.path.append(r"c:\Users\hp\Documents\claude_WO_API\livekit_agent")

from dotenv import load_dotenv
load_dotenv(r"c:\Users\hp\Documents\claude_WO_API\livekit_agent\.env", override=True)

from openai import OpenAI
from agent import build_instructions

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY)
MODEL = "gpt-4o-mini"

# Read FAQ knowledge base
with open(r"c:\Users\hp\Documents\claude_WO_API\livekit_agent\knowledge\faq_and_flow.txt", "r", encoding="utf-8") as f:
    FAQ_TEXT = f.read()

# Parse queries from stress_test.md dynamically
QUERIES = []
with open(r"c:\Users\hp\Documents\claude_WO_API\livekit_agent\tests\stress_test.md", "r", encoding="utf-8") as f:
    for line in f:
        if line.startswith("|") and not line.startswith("| # |") and not line.startswith("|---|"):
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 6:
                try:
                    q_id = int(parts[1])
                    # Clean up quotes and annotations from the text
                    caller_says = parts[2]
                    if ']' in caller_says and caller_says.startswith('*['):
                        caller_says = caller_says.split('] "')[-1]
                    caller_says = caller_says.strip('*" ')
                    
                    category = parts[3]
                    sub = parts[4]
                    QUERIES.append({
                        "id": q_id,
                        "caller_says": caller_says,
                        "category": category,
                        "sub": sub
                    })
                except ValueError:
                    pass

# Simulated customer persona: cooperative, will agree to resolution on turn 3
CUSTOMER_SYSTEM = """You are a Vetic pet care customer speaking on an outbound call.
You raised a concern that you'll state when asked. You are polite but firm.
You answer questions concisely. When the agent offers a resolution, you respond naturally
(acknowledge it, ask a brief follow-up if needed, then say 'ok that works' or 'thank you').
Keep your responses to 1-2 sentences max. Do NOT end the call yourself."""

def run_multiturn_test(query: dict) -> dict:
    """Run a full multi-turn conversation for a single query and return the transcript."""
    app_type = "Grooming" if "grooming" in query["category"].lower() else "OPD"
    user_context = (
        f"Customer Record:\n- Owner name: TestUser\n- Pet name: Buddy\n"
        f"- Last service / appointment type: {app_type}\n"
        f"- Last appointment date: July 18, 2025\n- Clinic: Vetic Noida, Sector 49"
    )
    
    agent_system = build_instructions(user_context, "TestUser")
    agent_system += "\n\nKNOWLEDGE BASE (faq_and_flow.txt):\n" + FAQ_TEXT
    
    agent_messages = [{"role": "system", "content": agent_system}]
    customer_messages = [
        {"role": "system", "content": CUSTOMER_SYSTEM},
        {"role": "user", "content": f"Your concern to raise is: '{query['caller_says']}'. "
                                     f"The category of your issue is: {query['category']}."}
    ]
    
    transcript = []
    resolution = "Not reached"
    MAX_TURNS = 8  # Max agent/customer turns each

    # Agent greets first
    agent_resp = client.chat.completions.create(
        model=MODEL,
        messages=agent_messages + [{"role": "user", "content": "(Call connected. Start the call with your greeting.)"}],
        max_tokens=120,
        temperature=0.0
    )
    agent_text = agent_resp.choices[0].message.content.strip()
    if "end_call" in agent_text.lower():
        agent_text = re.sub(r'\bend_call\b', '', agent_text).strip()
    
    transcript.append(f"**Aarya (Agent):** {agent_text}")
    agent_messages.append({"role": "assistant", "content": agent_text})
    customer_messages.append({"role": "user", "content": f"Agent said: {agent_text}\nRespond naturally as the customer."})

    for turn in range(MAX_TURNS):
        # Customer turn
        cust_resp = client.chat.completions.create(
            model=MODEL,
            messages=customer_messages,
            max_tokens=80,
            temperature=0.5
        )
        cust_text = cust_resp.choices[0].message.content.strip()
        transcript.append(f"**Customer:** {cust_text}")
        customer_messages.append({"role": "assistant", "content": cust_text})
        agent_messages.append({"role": "user", "content": cust_text})

        # Agent turn
        agent_resp = client.chat.completions.create(
            model=MODEL,
            messages=agent_messages,
            max_tokens=150,
            temperature=0.0
        )
        agent_text = agent_resp.choices[0].message.content.strip()
        
        # Detect if end_call was triggered
        call_ended = "end_call" in agent_text.lower()
        agent_text_clean = re.sub(r'\bend_call\b', '[CALL ENDED]', agent_text).strip()
        transcript.append(f"**Aarya (Agent):** {agent_text_clean}")
        
        # Capture resolution: look for resolution keywords in agent messages
        res_keywords = ["escalation ticket", "video consultation", "OPD", "booked", 
                        "callback", "resolution within", "notification 5 minutes", 
                        "raise this", "follow up", "clinical team"]
        if any(kw.lower() in agent_text.lower() for kw in res_keywords):
            resolution = agent_text_clean
        
        agent_messages.append({"role": "assistant", "content": agent_text})
        customer_messages.append({"role": "user", "content": f"Agent said: {agent_text_clean}\nRespond naturally. If the call ended or the agent gave a resolution, just say 'Thank you, goodbye.'"})

        if call_ended or "goodbye" in agent_text.lower() or "[CALL ENDED]" in agent_text_clean:
            break
        
        # Stop if customer says goodbye
        if "goodbye" in cust_text.lower() or "thank you, bye" in cust_text.lower():
            break

    return {
        "id": query["id"],
        "category": query["category"],
        "sub": query["sub"],
        "caller_says": query["caller_says"],
        "transcript": "\n\n".join(transcript),
        "resolution": resolution
    }


def main():
    print(f"Running multi-turn stress test on {len(QUERIES)} queries...\n")
    all_results = []
    
    for q in QUERIES:
        print(f"Testing Query {q['id']}: {q['category']} — {q['sub']}...")
        result = run_multiturn_test(q)
        all_results.append(result)

    # Build markdown output
    out = ["# Vetic Agent Stress Test — Full Transcripts & Resolutions\n"]
    out.append("---\n")
    
    # Summary table
    out.append("## Summary Table\n")
    out.append("| # | Category | Sub-issue | Resolution Offered |\n")
    out.append("|---|---|---|---|\n")
    for r in all_results:
        res_short = r["resolution"][:120].replace("\n", " ") + ("..." if len(r["resolution"]) > 120 else "")
        out.append(f"| {r['id']} | {r['category']} | {r['sub']} | {res_short} |\n")
    
    out.append("\n---\n")
    
    # Full transcripts
    out.append("## Full Call Transcripts\n")
    for r in all_results:
        out.append(f"### Query {r['id']}: {r['category']} — {r['sub']}\n")
        out.append(f"> **Caller says:** \"{r['caller_says']}\"\n\n")
        out.append("**Transcript:**\n\n")
        out.append(r["transcript"])
        out.append("\n\n")
        out.append(f"**📋 Final Resolution Offered:**\n> {r['resolution']}\n")
        out.append("\n---\n")

    out_path = r"c:\Users\hp\Documents\claude_WO_API\livekit_agent\tests\results\stress_test_results_full.md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("".join(out))
    
    print(f"\nDone. Full results saved to: {out_path}")


if __name__ == "__main__":
    main()
