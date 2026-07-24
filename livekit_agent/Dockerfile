# Vetic LiveKit voice agent worker.
# Build/run from this directory (livekit_agent/) as the Docker build context.
FROM python:3.12-slim

# ffmpeg is needed at the OS level for imageio-ffmpeg's bundled binary to link
# against on some platforms; libsndfile1 covers Sarvam/silero audio I/O deps.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent.py agent_inbound.py vetic_db.py run.py ./
COPY knowledge/ ./knowledge/
COPY knowledge_db/ ./knowledge_db/
COPY data/ ./data/

# livekit-agents' WorkerOptions defaults to health-check port 8081 in `start`
# (production) mode.
EXPOSE 8081

ENV AGENT_ENTRYPOINT=inbound
CMD ["python", "run.py"]
