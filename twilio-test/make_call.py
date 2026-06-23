import os
from dotenv import load_dotenv
from twilio.rest import Client

load_dotenv()

# Credentials are read from the environment (.env). Never hardcode them.
# Find your Account SID and Auth Token at twilio.com/console
account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
auth_token = os.environ.get("TWILIO_AUTH_TOKEN")

if not account_sid or not auth_token:
    raise SystemExit(
        "Missing Twilio credentials. Set TWILIO_ACCOUNT_SID and "
        "TWILIO_AUTH_TOKEN in your .env file."
    )

client = Client(account_sid, auth_token)

try:
    call = client.calls.create(
        # This URL returns TwiML with a simple voice message.
        # You can replace it with your own TwiML URL or use twimlets.
        url="https://matching-percentage-soldier-came.trycloudflare.com/incoming_call",
        to="+919552443385",    # Replace with the verified phone number you want to call
        from_="+17373671890",  # Replace with your purchased Twilio phone number
        record=True            # Record the full conversation
    )
    print(f"Call initiated successfully! Call SID: {call.sid}")
except Exception as e:
    print(f"Failed to initiate call: {e}")
