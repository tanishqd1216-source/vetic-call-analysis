"""Best-effort call-event reporting to the ai-call-agent dashboard backend.

Purely additive observability: nothing here reads anything back or affects
call handling. A backend outage, a slow network, or a malformed payload must
never break a live call, so every failure is caught and logged, never raised.
"""

import asyncio
import logging
import os

import httpx

logger = logging.getLogger("event_reporter")

EVENTS_API_URL = os.environ.get("EVENTS_API_URL", "http://localhost:4000/api/events")

# Keep strong references to fire-and-forget tasks so they aren't garbage
# collected mid-flight — same pattern already used for background_tasks in
# agent_outbound.py / agent_inbound.py.
_background_tasks: set = set()


async def report_event(call_id: str, direction: str, event_type: str, payload: dict | None = None) -> None:
    body = {
        "callId": call_id,
        "direction": direction,
        "eventType": event_type,
        "payload": payload or {},
    }
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.post(EVENTS_API_URL, json=body)
    except Exception as e:
        logger.warning(f"event_reporter: failed to report {event_type} for {call_id}: {e}")


def fire_event(call_id: str, direction: str, event_type: str, payload: dict | None = None) -> None:
    """Schedule report_event without blocking the caller. Safe to call from
    both async call sites and the synchronous _on_metrics callback."""
    try:
        loop = asyncio.get_event_loop()
        task = loop.create_task(report_event(call_id, direction, event_type, payload))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
    except Exception as e:
        logger.warning(f"event_reporter: could not schedule {event_type} for {call_id}: {e}")
