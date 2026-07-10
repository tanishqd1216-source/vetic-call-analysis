import asyncio
import os
from dotenv import load_dotenv
from livekit.plugins import openai
from livekit.agents import llm

# We must mock some LiveKit dependencies that agent.py uses
import agent
from agent import VeticAgent, build_instructions

async def test_llm():
    load_dotenv()
    load_dotenv("../.env")
    
    instructions = build_instructions("Test context")
    vetic = VeticAgent(instructions=instructions, greeting="Hello")
    
    # Extract tools
    fnc_ctx = llm.ToolContext()
    # To register methods decorated with @function_tool, we can try:
    try:
        fnc_ctx.register(vetic)
        print("Tools registered:", [k for k in fnc_ctx.ai_functions])
    except Exception as e:
        print("Failed to register tools:", e)

    my_llm = openai.LLM(
        model="llama-3.1-8b-instant",
        base_url="https://api.groq.com/openai/v1",
        api_key=os.environ.get("GROK")
    )
    
    ctx = llm.ChatContext()
    ctx.messages.append(llm.ChatMessage(role="system", content=instructions))
    ctx.messages.append(llm.ChatMessage(role="user", content="What is your return policy?"))
    
    stream = my_llm.chat(chat_ctx=ctx, fnc_ctx=fnc_ctx)
    print("Agent Response: ", end="", flush=True)
    async for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            print(chunk.choices[0].delta.content, end="", flush=True)
    print("\n")

if __name__ == "__main__":
    asyncio.run(test_llm())
