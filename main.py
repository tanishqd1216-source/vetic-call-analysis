import os
from dotenv import load_dotenv
import anthropic

load_dotenv()  # Load ANTHROPIC_API_KEY from .env file
client = anthropic.Anthropic()  # Automatically reads ANTHROPIC_API_KEY from environment

import json

HISTORY_FILE = "conversation_history.json"

conversation_history = []
if os.path.exists(HISTORY_FILE):
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            conversation_history = json.load(f)
        print(f"Loaded {len(conversation_history)} past messages from history.")
    except Exception as e:
        print(f"Could not load history: {e}")

while True:
    try:
        user_input = input("\nYou: ").strip()
    except KeyboardInterrupt:
        print("\nExiting chat...")
        break
    except EOFError:
        break

    if user_input.lower() in ["exit", "quit"]:
        break
        
    if user_input.lower() == "clear":
        conversation_history = []
        if os.path.exists(HISTORY_FILE):
            os.remove(HISTORY_FILE)
        print("Conversation history cleared.")
        continue

    if not user_input:
        print("Message cannot be empty. Please enter a valid message.")
        continue

    conversation_history.append({
        "role": "user",
        "content": user_input
    })

    full_reply = ""

    while True:

        current_chunk = ""

        try:
            with client.messages.stream(
                model="claude-sonnet-4-5-20250929",
                max_tokens=8192,
                messages=conversation_history
            ) as stream:

                for text in stream.text_stream:
                    print(text, end="", flush=True)
                    current_chunk += text

                final_message = stream.get_final_message()

            full_reply += current_chunk

            if final_message.stop_reason == "max_tokens":
                conversation_history.append({
                    "role": "assistant",
                    "content": current_chunk
                })
                conversation_history.append({
                    "role": "user",
                    "content": "Continue exactly where you stopped. Do not repeat anything."
                })
                print("\n\n--- Continuing ---\n")
            else:
                break

        except KeyboardInterrupt:
            print("\n\n[Interrupted by user]")
            full_reply += current_chunk
            break

    print("\n")

    conversation_history.append({
        "role": "assistant",
        "content": full_reply
    })

    # Save history to file after every turn
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(conversation_history, f, indent=2)
    except Exception as e:
        print(f"\nWarning: Could not save history: {e}")