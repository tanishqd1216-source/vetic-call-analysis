import os
from dotenv import load_dotenv
from twilio.rest import Client

load_dotenv(override=True)

# Account SID and Auth Token from twilio.com/console — set them in .env, never hardcode
account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
if not account_sid or not auth_token:
    raise RuntimeError("TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN not set. Add them to your .env file.")

client = Client(account_sid, auth_token)
print(f"Using Account SID: {account_sid[:5]}...{account_sid[-5:]}")

try:
    base_url = "https://generated-launches-seeker-enforcement.trycloudflare.com"
    call = client.calls.create(
        url=f"{base_url}/incoming_call",
        to="+918273004988",    # Replace with the verified phone number you want to call
        from_="+13198358061",  # Replace with your purchased Twilio phone number
        record=True,           # Record the full conversation
        recording_status_callback=f"{base_url}/recording_status",
        recording_status_callback_event=['completed']
    )
    print(f"Call initiated successfully! Call SID: {call.sid}")
except Exception as e:
    print(f"Failed to initiate call: {e}")
