import os
from dotenv import load_dotenv
import requests

load_dotenv()
SARVAM_API_KEY = os.environ.get("SARVAM_API_KEY")

text = "Hello, testing."
url = "https://api.sarvam.ai/text-to-speech"
headers = {"api-subscription-key": SARVAM_API_KEY, "Content-Type": "application/json"}
payload = {"inputs": [text], "target_language_code": "hi-IN", "speaker": "arya", "pace": 1.05, "speech_sample_rate": 8000, "enable_preprocessing": True, "model": "bulbul:v3"}
response = requests.post(url, json=payload, headers=headers)

print(f"Status: {response.status_code}")
print(f"Response: {response.text}")
