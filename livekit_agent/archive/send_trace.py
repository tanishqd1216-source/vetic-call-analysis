# send_trace.py – minimal LangSmith tracing example
import os
from langsmith import Client

# Ensure the LangSmith API key is in the environment (add to .env)
api_key = os.getenv("LANGSMITH_API_KEY")
if not api_key:
    raise RuntimeError(
        "LANGSMITH_API_KEY not found in environment. Add it to your .env file."
    )

client = Client(api_key=api_key)

# Create a run – you can change name/project as needed
run = client.create_run(
    name="demo-run",
    project_name="demo-project",
    tags=["demo", "quick-test"],
)

# Log some example messages
client.log(run.id, {"message": "Agent started", "step": 1})
client.log(run.id, {"message": "User said hi", "step": 2})
client.log(run.id, {"message": "Agent replied", "step": 3})

# Finish the run
client.end_run(run.id, status="success")
print(f"✅ Trace sent! View at https://smith.langchain.com/run/{run.id}")
