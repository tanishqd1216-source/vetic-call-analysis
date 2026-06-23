import os
import requests
import base64
from dotenv import load_dotenv

load_dotenv()
api_key = os.environ.get("SARVAM_API_KEY")

def test_tts():
    print("Testing TTS...")
    url = "https://api.sarvam.ai/text-to-speech"
    headers = {
        "api-subscription-key": api_key,
        "Content-Type": "application/json"
    }
    payload = {
        "inputs": ["Hello, how are you today?"],
        "target_language_code": "en-IN",
        "speaker": "meera",
        "model": "bulbul:v1"
    }
    resp = requests.post(url, json=payload, headers=headers)
    print(resp.status_code, resp.text[:200])

if __name__ == "__main__":
    test_tts()
