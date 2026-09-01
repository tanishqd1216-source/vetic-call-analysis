import os
import csv
import json
import asyncio
import wave
import requests
import base64
import chromadb
from openai import AsyncOpenAI
from dotenv import load_dotenv

from agent import build_instructions

load_dotenv(override=True)
load_dotenv("../.env", override=True)

GROQ_API_KEY = os.environ.get("GROK")
SARVAM_API_KEY = os.environ.get("SARVAM_API_KEY")

aclient = AsyncOpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")
MODEL = "llama-3.1-8b-instant"

# Tanishq Context from sample_ticket_summary.sql
USER_CONTEXT = """Customer Record:
Name: Tanishq
Pet: Kittu
Recent Visit: Grooming on April 24 at Vetic Pet Care, Sector 49, Noida
Ticket ID: 352104
Phone: +919557814441"""

SCENARIOS = [
    {
        "name": "Normal",
        "prompt": "You are Tanishq. You had a grooming appointment for your pet Kittu at Vetic Noida. You were slightly unhappy because they were running 30 minutes late and the bath wasn't done well. Be polite but firm. You want them to acknowledge the delay."
    },
    {
        "name": "Angry",
        "prompt": "You are Tanishq. You are FURIOUS. Your pet Kittu went for grooming at Vetic Noida and got a rash from the shampoo. Act extremely angry, yell, and demand a full refund and a free vet checkup immediately. Threaten to post on social media."
    }
]

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "consult_background_worker",
            "description": "Use this tool whenever the user asks a complex medical question, policy question, or data lookup. You MUST simultaneously say a filler phrase like 'Let me check that for you'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The specific question or topic."}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "end_call",
            "description": "End the call when the customer signals the conversation is over.",
            "parameters": {"type": "object", "properties": {}}
        }
    }
]

# ChromaDB
try:
    db_path = os.path.join(os.path.dirname(__file__), "knowledge_db")
    chroma_client = chromadb.PersistentClient(path=db_path)
    chroma_collection = chroma_client.get_collection("vetic_knowledge")
except Exception:
    chroma_collection = None

import re

def generate_tts_sarvam(text: str, speaker: str, filename: str):
    """Uses Sarvam TTS and saves a WAV file, chunking text if it's over 500 characters."""
    url = "https://api.sarvam.ai/text-to-speech"
    headers = {"API-Subscription-Key": SARVAM_API_KEY, "Content-Type": "application/json"}
    
    # Simple sentence splitting to try and keep chunks under 500 chars
    sentences = re.split(r'(?<=[.!?]) +', text)
    chunks = []
    current_chunk = ""
    for sentence in sentences:
        if len(current_chunk) + len(sentence) < 450:
            current_chunk += sentence + " "
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
            current_chunk = sentence + " "
    if current_chunk:
        chunks.append(current_chunk.strip())
        
    chunk_wavs = []
    success = True
    
    for i, chunk in enumerate(chunks):
        if not chunk: continue
        payload = {
            "inputs": [chunk],
            "target_language_code": "en-IN",
            "speaker": speaker,
            "pace": 1.0,
            "speech_sample_rate": 22050,
            "enable_preprocessing": True,
            "model": "bulbul:v3"
        }
        chunk_file = f"{filename}_chunk_{i}.wav"
        try:
            resp = requests.post(url, json=payload, headers=headers)
            if resp.status_code == 200:
                audio_bytes = base64.b64decode(resp.json()["audios"][0])
                with open(chunk_file, "wb") as f:
                    f.write(audio_bytes)
                chunk_wavs.append(chunk_file)
            else:
                print(f"TTS Error for {speaker}: {resp.text}")
                success = False
        except Exception as e:
            print(f"TTS Exception: {e}")
            success = False
            
    if chunk_wavs:
        stitch_wavs(chunk_wavs, filename)
        for cf in chunk_wavs:
            if os.path.exists(cf):
                os.remove(cf)
    
    return success

def stitch_wavs(wav_files, output_file):
    """Concatenates multiple WAV files into one."""
    with wave.open(output_file, 'wb') as outfile:
        for i, wav_file in enumerate(wav_files):
            if not os.path.exists(wav_file):
                continue
            with wave.open(wav_file, 'rb') as infile:
                if i == 0:
                    outfile.setparams(infile.getparams())
                outfile.writeframes(infile.readframes(infile.getnframes()))

async def run_simulation(scenario: dict):
    print(f"\n--- Starting {scenario['name']} Simulation ---")
    
    instructions = build_instructions(USER_CONTEXT)
    aarya_messages = [{"role": "system", "content": instructions}]
    tanishq_messages = [{"role": "system", "content": scenario["prompt"]}]
    
    aarya_opening = "Good morning, am I speaking with Tanishq? This is Aarya calling from Vetic."
    aarya_messages.append({"role": "assistant", "content": aarya_opening})
    tanishq_messages.append({"role": "user", "content": f"[Agent just said]: {aarya_opening}\n\nNow respond as Tanishq."})
    
    print(f"AARYA: {aarya_opening}")
    
    wav_files = []
    turn_counter = 0
    
    # Generate Aarya's opening audio
    fname = f"temp_{scenario['name']}_{turn_counter}_aarya.wav"
    if generate_tts_sarvam(aarya_opening, "priya", fname):
        wav_files.append(fname)
        
    for turn in range(6): # Max 6 turns
        turn_counter += 1
        
        # 1. Tanishq Speaks
        resp = await aclient.chat.completions.create(
            model=MODEL, messages=tanishq_messages, max_tokens=150, temperature=0.7
        )
        tanishq_text = resp.choices[0].message.content
        print(f"TANISHQ: {tanishq_text}")
        
        tanishq_messages.append({"role": "assistant", "content": tanishq_text})
        aarya_messages.append({"role": "user", "content": tanishq_text})
        
        fname = f"temp_{scenario['name']}_{turn_counter}_tanishq.wav"
        if generate_tts_sarvam(tanishq_text, "rahul", fname): # rahul is a male voice
            wav_files.append(fname)
            
        # 2. Aarya Responds
        try:
            aarya_response = await aclient.chat.completions.create(
                model=MODEL, messages=aarya_messages, tools=TOOLS, max_tokens=200, temperature=0.7
            )
            msg = aarya_response.choices[0].message
        except Exception as e:
            err_str = str(e)
            if "end_call" in err_str:
                print("Call ended by Agent via raw function string.")
                break
            else:
                print(f"Error during Aarya response: {e}")
                break
                
        final_aarya_text = msg.content or ""
        
        if msg.tool_calls:
            aarya_messages.append(msg)
            for tool_call in msg.tool_calls:
                if tool_call.function.name == "consult_background_worker":
                    args = json.loads(tool_call.function.arguments)
                    query = args.get("query", "")
                    
                    result = "I could not find information about that."
                    if chroma_collection:
                        try:
                            results = chroma_collection.query(query_texts=[query], n_results=3)
                            if results and results["documents"] and results["documents"][0]:
                                result = "Here is the relevant knowledge:\n" + "\n\n".join(results["documents"][0])
                        except Exception:
                            pass
                            
                    aarya_messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result})
                    
                elif tool_call.function.name == "end_call":
                    aarya_messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": "Call ended."})
                    final_aarya_text += " [Hangs up]"
                    
            if "[Hangs up]" not in final_aarya_text:
                try:
                    followup = await aclient.chat.completions.create(
                        model=MODEL, messages=aarya_messages, tools=TOOLS, max_tokens=200, temperature=0.7
                    )
                    final_aarya_text = followup.choices[0].message.content
                except Exception as e:
                    err_str = str(e)
                    if "end_call" in err_str:
                        final_aarya_text = "[Hangs up]"
                    else:
                        print(f"Error during Aarya followup response: {e}")
                
        print(f"AARYA: {final_aarya_text}")
        aarya_messages.append({"role": "assistant", "content": final_aarya_text})
        
        # Audio for Aarya
        clean_text = final_aarya_text.replace("[Hangs up]", "").strip()
        if clean_text:
            fname = f"temp_{scenario['name']}_{turn_counter}_aarya.wav"
            if generate_tts_sarvam(clean_text, "priya", fname):
                wav_files.append(fname)
                
        tanishq_messages.append({"role": "user", "content": f"[Agent just said]: {clean_text}\n\nNow respond as Tanishq."})
        
        if "[Hangs up]" in final_aarya_text:
            print("Call ended by Agent.")
            break
            
    # Stitch audio
    output_filename = f"tanishq_{scenario['name'].lower()}_call.wav"
    stitch_wavs(wav_files, output_filename)
    print(f"Saved full recorded call to {output_filename}")
    
    # Cleanup temps
    for f in wav_files:
        if os.path.exists(f):
            os.remove(f)

async def main():
    if not SARVAM_API_KEY:
        print("SARVAM_API_KEY not found in .env!")
        return
        
    for scenario in SCENARIOS:
        await run_simulation(scenario)
        print("Waiting 10 seconds before next scenario...")
        await asyncio.sleep(10)
        
    print("All simulations complete.")

if __name__ == "__main__":
    asyncio.run(main())
