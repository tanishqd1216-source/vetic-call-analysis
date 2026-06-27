import os
from dotenv import load_dotenv
from anthropic import Anthropic

load_dotenv()
client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

try:
    models = client.models.list()
    for model in models.data:
        print(model.id)
except Exception as e:
    print(f"Failed to list models: {e}")
