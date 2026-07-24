import os
import requests
import base64
from dotenv import load_dotenv

load_dotenv()
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY")

def generate():
    url = "https://api.sarvam.ai/text-to-speech"
    payload = {
        "inputs": ["Good day! Welcome to Vetic Pet Care. How can I help you today?"],
        "target_language_code": "en-IN",
        "speaker": "meera",
        "pitch": 0,
        "pace": 1.30,
        "loudness": 1.5,
        "speech_sample_rate": 8000,
        "enable_preprocessing": True,
        "model": "bulbul:v1"
    }
    headers = {"api-subscription-key": SARVAM_API_KEY, "Content-Type": "application/json"}
    response = requests.post(url, json=payload, headers=headers)
    
    if response.status_code == 200:
        audio_b64 = response.json()["audios"][0]
        with open("greeting.wav", "wb") as f:
            f.write(base64.b64decode(audio_b64))
        print("greeting.wav generated")
    else:
        print("Error:", response.text)

if __name__ == "__main__":
    generate()
