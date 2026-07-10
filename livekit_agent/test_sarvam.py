import os
import requests
import base64
from dotenv import load_dotenv

def test_sarvam():
    load_dotenv(override=True)
    load_dotenv("../.env", override=True)
    api_key = os.environ.get("SARVAM_API_KEY")
    print(f"API Key: {api_key[:10]}...")
    
    url = "https://api.sarvam.ai/text-to-speech"
    headers = {
        "API-Subscription-Key": api_key,
        "Content-Type": "application/json"
    }
    payload = {
        "inputs": ["Hello from Sarvam AI. Testing the TTS endpoint."],
        "target_language_code": "en-IN",
        "speaker": "meera",
        "pitch": 0,
        "pace": 1.0,
        "loudness": 1.5,
        "speech_sample_rate": 22050,
        "enable_preprocessing": True,
        "model": "bulbul:v3"
    }
    
    response = requests.post(url, json=payload, headers=headers)
    print(response.status_code)
    
    if response.status_code == 200:
        data = response.json()
        b64_audio = data["audios"][0]
        audio_bytes = base64.b64decode(b64_audio)
        with open("test_sarvam.wav", "wb") as f:
            f.write(audio_bytes)
        print("Success! Wrote test_sarvam.wav")
    else:
        print("Error:", response.text)

if __name__ == "__main__":
    test_sarvam()
