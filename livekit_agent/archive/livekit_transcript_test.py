import os
import re
import csv
import json
import asyncio
import chromadb
from pypdf import PdfReader
from openai import AsyncOpenAI
from dotenv import load_dotenv

# Import the exact rules and instructions builder from agent.py
from agent import build_instructions, get_user_context

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

# Initialize Vector DB
try:
    db_path = os.path.join(os.path.dirname(__file__), "knowledge_db")
    chroma_client = chromadb.PersistentClient(path=db_path)
    chroma_collection = chroma_client.get_collection("vetic_knowledge")
except Exception as e:
    print(f"Warning: Failed to load ChromaDB: {e}. Run init_vector_db.py first.")
    chroma_collection = None

# Define the tools available to Aarya (matching agent.py)
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "consult_background_worker",
            "description": "Use this tool whenever the user asks a complex medical question, policy question, or data lookup. You MUST simultaneously say a filler phrase like 'Let me check that for you'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The specific question or topic to look up in the database."
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "end_call",
            "description": "End the call when the customer signals the conversation is over (e.g. 'no, that's all', 'thank you', 'bye').",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    }
]

def extract_conversations_from_pdf(pdf_path):
    reader = PdfReader(pdf_path)
    text = ""
    for page in reader.pages:
        text += page.extract_text() + " "
    
    text = text.replace('\n', ' ')
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
        
    return conversations

def parse_transcript_block(text_block):
    """Extracts a list of customer utterances from a single transcript block."""
    pattern = re.compile(r'CUSTOMER:\s*(.*?)(?=AGENT:|$)', re.IGNORECASE)
    matches = pattern.findall(text_block)
    return [m.strip() for m in matches if m.strip()]

async def chat_with_tools(messages: list) -> str:
    """Handles chat completions including recursive tool calls."""
    response = await aclient.chat.completions.create(
        model=MODEL,
        messages=messages,
        tools=TOOLS,
        max_tokens=200,
        temperature=0.7,
    )
    
    msg = response.choices[0].message
    if msg.tool_calls:
        # Append the assistant's tool call message
        messages.append(msg)
        
        final_spoken_text = ""
        
        for tool_call in msg.tool_calls:
            if tool_call.function.name == "consult_background_worker":
                args = json.loads(tool_call.function.arguments)
                query = args.get("query", "")
                
                # Execute the RAG lookup just like agent.py's background worker
                result = "I could not find any specific information about that in the knowledge base."
                if chroma_collection:
                    try:
                        results = chroma_collection.query(query_texts=[query], n_results=3)
                        if results and results["documents"] and results["documents"][0]:
                            chunks = results["documents"][0]
                            result = "Here is the most relevant company knowledge I found:\n" + "\n\n".join(chunks)
                    except Exception as e:
                        result = f"Error accessing knowledge base: {e}"
                        
                # Provide the tool response back to the LLM
                tool_msg = {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": f"BACKGROUND WORKER HAS RETURNED THE FOLLOWING RESULT: {result}. You can now answer the user's question seamlessly."
                }
                messages.append(tool_msg)
                
            elif tool_call.function.name == "end_call":
                tool_msg = {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": "Call ended successfully."
                }
                messages.append(tool_msg)
                final_spoken_text = "[Agent calls end_call tool and hangs up]"
        
        # Get the final response after tool execution
        if final_spoken_text != "[Agent calls end_call tool and hangs up]":
            followup_response = await aclient.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=TOOLS,
                max_tokens=200,
                temperature=0.7,
            )
            final_spoken_text = followup_response.choices[0].message.content
            
        return f"[Used Tool(s)] {final_spoken_text}"
    else:
        return msg.content

async def run_simulation(test_id: int, customer_turns: list):
    print(f"--- Starting LiveKit-Style Simulation {test_id} ({len(customer_turns)} turns) ---")
    
    # Generate the exact instructions string from agent.py
    # Provide a dummy context for the simulation
    dummy_context = "Customer Record:\nName: Test User\nPet: Buddy (Dog)\nRecent Visit: Grooming on July 10"
    instructions = build_instructions(dummy_context)
    
    aarya_messages = [{"role": "system", "content": instructions}]
    transcript = []
    
    # Aarya's opening line
    aarya_response = "Good morning, am I speaking with Test User? This is Aarya calling from Vetic."
    transcript.append(f"AARYA: {aarya_response}")
    aarya_messages.append({"role": "assistant", "content": aarya_response})
    
    for turn_text in customer_turns:
        transcript.append(f"USER (From PDF): {turn_text}")
        aarya_messages.append({"role": "user", "content": turn_text})
        
        try:
            aarya_text = await chat_with_tools(aarya_messages)
        except Exception as e:
            aarya_text = f"[Aarya Failed to generate: {e}]"
            
        transcript.append(f"AARYA: {aarya_text}")
        aarya_messages.append({"role": "assistant", "content": aarya_text})
        
        if "end_call" in aarya_text:
            break
        
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
        
    conversations = extract_conversations_from_pdf(pdf_path)
    print(f"Extracted {len(conversations)} conversation blocks from the PDF. Running simulation on the first 5 valid ones.")
    
    results = []
    valid_count = 0
    
    for i, conv_text in enumerate(conversations):
        if valid_count >= 5:
            break
            
        conv_text_clean = conv_text.replace('\n', ' ')
        customer_turns = parse_transcript_block(conv_text_clean)
        
        if not customer_turns:
            continue
            
        valid_count += 1
        res = await run_simulation(valid_count, customer_turns)
        results.append(res)
        
        # Write progressively
        with open("livekit_stress_test_results.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["Test ID", "Total Turns", "Transcript"])
            writer.writeheader()
            writer.writerows(results)
            
        await asyncio.sleep(2) # rate limit prevention
        
    print(f"Done! Saved {len(results)} simulated conversations to livekit_stress_test_results.csv")

if __name__ == "__main__":
    asyncio.run(main())
