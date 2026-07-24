import os
import re
import csv
import json
import asyncio
from pypdf import PdfReader
from openai import AsyncOpenAI
from dotenv import load_dotenv

# Import the exact rules Aarya uses in production
from agent import BASE_RULES

# Load both the local and the root .env files
load_dotenv()
load_dotenv("../.env")

GROQ_API_KEY = os.environ.get("GROK")
if not GROQ_API_KEY:
    raise ValueError("GROK not found in .env. Please add it!")

aclient = AsyncOpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1",
)
MODEL = "llama-3.1-8b-instant"

def extract_conversations_from_pdf(pdf_path):
    reader = PdfReader(pdf_path)
    text = ""
    for page in reader.pages:
        text += page.extract_text() + " "
    
    # Clean up newlines to make regex matching easier
    text = text.replace('\n', ' ')
    
    # Split text by looking for "AGENT:" which usually starts a transcript block
    # But wait, there are multiple conversations. A conversation usually starts with a summary 
    # paragraph followed by "AGENT:".
    # Let's split by "AGENT: Hello" or "AGENT: Good" which are common greetings.
    # Actually, a simpler way is to just find all sequences of AGENT/CUSTOMER interactions.
    # Let's find all CUSTOMER turns.
    
    # A better approach: split the whole text into chunks that look like conversations.
    # We can split by "AGENT: " and then reconstruct, or just find all matches of CUSTOMER:
    
    conversations = []
    
    # Let's find blocks of text that look like transcripts.
    # In the PDF, each transcript starts after the summary. We can split by "AGENT: " but that splits every turn.
    # Let's use a heuristic: if we see "AGENT:" followed by a greeting, it might be a new call.
    # To be safe, let's just extract every single CUSTOMER response in order, and group them if they are close together.
    # Better yet, let's split by double newlines if the PDF had them, but we stripped them.
    pass

def parse_transcript_block(text_block):
    """Extracts a list of customer utterances from a single transcript block."""
    # Find all CUSTOMER: <text> up to the next AGENT: or end of string
    pattern = re.compile(r'CUSTOMER:\s*(.*?)(?=AGENT:|$)', re.IGNORECASE)
    matches = pattern.findall(text_block)
    return [m.strip() for m in matches if m.strip()]

async def chat(messages: list, system: str = None, max_tokens: int = 150) -> str:
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.extend(messages)
    
    resp = await aclient.chat.completions.create(
        model=MODEL,
        messages=msgs,
        max_tokens=max_tokens,
        temperature=0.7,
    )
    return resp.choices[0].message.content

async def run_simulation(test_id: int, customer_turns: list):
    print(f"--- Starting Simulation {test_id} ({len(customer_turns)} turns) ---")
    
    aarya_messages = []
    transcript = []
    
    # Aarya's opening line
    aarya_response = "Good morning, this is Aarya calling from Vetic. May I confirm who I'm speaking with?"
    transcript.append(f"AARYA: {aarya_response}")
    aarya_messages.append({"role": "assistant", "content": aarya_response})
    
    for turn_text in customer_turns:
        transcript.append(f"USER (From PDF): {turn_text}")
        aarya_messages.append({"role": "user", "content": turn_text})
        
        try:
            aarya_text = await chat(aarya_messages, system=BASE_RULES, max_tokens=150)
        except Exception as e:
            aarya_text = f"[Aarya Failed to generate: {e}]"
            
        transcript.append(f"AARYA: {aarya_text}")
        aarya_messages.append({"role": "assistant", "content": aarya_text})
        
    full_transcript = "\n".join(transcript)
    print(f"Simulation {test_id} complete.\n")
    
    return {
        "Test ID": test_id,
        "Total Turns": len(customer_turns),
        "Transcript": full_transcript,
    }

async def main():
    pdf_path = r"c:\Users\hp\Desktop\sample data\Summary - transcript.pdf"
    if not os.path.exists(pdf_path):
        print(f"Error: {pdf_path} not found.")
        return
        
    reader = PdfReader(pdf_path)
    text = ""
    for page in reader.pages:
        text += page.extract_text() + "\n"
        
    # Split the text into conversations. 
    # In the PDF, conversations seem to be separated by a summary paragraph.
    # We can use regex to find blocks starting with "AGENT:" and ending before the next summary paragraph.
    # Actually, looking at the OCR, each summary starts a new paragraph, followed by a paragraph starting with "AGENT:".
    # Let's split by "AGENT:" but only if it's the START of a conversation.
    # A simpler way: just split the whole document by "AGENT:" and whenever we see "CUSTOMER:", we extract it. 
    # But we want to group them by conversation.
    # Let's split the text by "AGENT: Good morning" or "AGENT: Hello" or "AGENT: Hi" or "AGENT: Very good".
    
    split_pattern = re.compile(r'(AGENT:\s*(?:Hello|Good|Hi|Very good)[^\n]*?(?:CUSTOMER:|$))', re.IGNORECASE)
    parts = split_pattern.split(text)
    
    conversations = []
    current_conv = ""
    
    for part in parts:
        if split_pattern.match(part):
            if current_conv.strip():
                conversations.append(current_conv)
            current_conv = part
        else:
            current_conv += part
            
    if current_conv.strip():
        conversations.append(current_conv)
        
    print(f"Extracted {len(conversations)} conversation blocks from the PDF. Running simulation on the first 15 valid ones.")
    
    results = []
    valid_count = 0
    
    for i, conv_text in enumerate(conversations):
        if valid_count >= 15:
            break
            
        # Clean newlines inside the block for easier matching
        conv_text_clean = conv_text.replace('\n', ' ')
        customer_turns = parse_transcript_block(conv_text_clean)
        
        if not customer_turns:
            continue
            
        valid_count += 1
        res = await run_simulation(valid_count, customer_turns)
        results.append(res)
        
        # Write progressively
        with open("transcript_test_results.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["Test ID", "Total Turns", "Transcript"])
            writer.writeheader()
            writer.writerows(results)
            
        await asyncio.sleep(2) # rate limit prevention
        
    print(f"Done! Saved {len(results)} simulated conversations to transcript_test_results.csv")

if __name__ == "__main__":
    asyncio.run(main())
