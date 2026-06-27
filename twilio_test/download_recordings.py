import os
from dotenv import load_dotenv
from twilio.rest import Client
import requests

load_dotenv(override=True)

# Account SID and Auth Token from twilio.com/console — set them in .env, never hardcode
account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
if not account_sid or not auth_token:
    raise RuntimeError("TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN not set. Add them to your .env file.")

client = Client(account_sid, auth_token)

os.makedirs("recordings", exist_ok=True)

# Fetch the last 5 recordings
recordings = client.recordings.list(limit=5)

if not recordings:
    print("No recordings found.")

for record in recordings:
    # URL to download the audio (WAV format)
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Recordings/{record.sid}.mp3"

    print(f"Downloading {record.sid}.mp3 ...")
    response = requests.get(url, auth=(account_sid, auth_token))

    if response.status_code == 200:
        filepath = os.path.join("recordings", f"{record.sid}.mp3")
        with open(filepath, "wb") as f:
            f.write(response.content)
        print(f"Saved to {filepath}")
    else:
        print(f"Failed to download {record.sid}: {response.status_code}")
