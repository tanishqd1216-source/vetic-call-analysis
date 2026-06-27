"""
Lightweight, zero-latency RAG helper for the Vetic calling agent.

Design goal: keep per-turn latency low. There is NO embedding call and NO
network round-trip in the hot path. All knowledge JSON files are loaded once
at import time, and per-turn work is pure in-memory string building
(microseconds). The curated examples ride along inside the Claude call you
already make, so they add no extra step to the STT -> Claude -> TTS pipeline.

Usage (in server.py):

    from knowledge.rag import build_examples_block

    def get_system_prompt(scenario: str, stage: int) -> str:
        ...
        prompt = BASE_RULES + "\\n\\n" + stage_rules
        examples = build_examples_block(scenario, stage)   # "" if none
        if examples:
            prompt += "\\n" + examples
        return prompt

Scaling note: this injects ALL instances for the matching (scenario, stage).
That is ideal up to ~30 short instances. If a knowledge file grows into the
hundreds, add a local lexical pre-filter here (BM25 / token-overlap in pure
Python) to pick top-k before formatting -- still no network call.
"""

import os
import json

# Knowledge files live in the same directory as this module, resolved
# absolutely so loading works regardless of the process working directory.
_KNOWLEDGE_DIR = os.path.dirname(os.path.abspath(__file__))

# stage integer (as used in server.py) -> stage name (as used in the JSON)
STAGE_NAMES = {1: "introduction", 2: "context", 3: "resolution", 4: "conclusion"}


def _load_knowledge() -> dict:
    """Load every *.json knowledge file once. Returns {(scenario, stage): [instances]}."""
    kb = {}
    for fname in os.listdir(_KNOWLEDGE_DIR):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(_KNOWLEDGE_DIR, fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            key = (data.get("scenario", "general"), data.get("stage", "introduction"))
            kb[key] = data.get("instances", [])
        except Exception as e:
            # A malformed knowledge file must never crash the call server.
            print(f"[rag] Failed to load {fname}: {e}")
    return kb


# Loaded once at import. Reload the process to pick up edited knowledge files.
KNOWLEDGE = _load_knowledge()


def build_examples_block(scenario: str, stage: int) -> str:
    """
    Return a few-shot example block for the given scenario/stage, or "" if none.

    Pure string building -- safe to call on every turn with negligible cost.
    """
    instances = KNOWLEDGE.get((scenario, STAGE_NAMES.get(stage, "")), [])
    if not instances:
        return ""

    lines = [
        "",
        "EXAMPLE SITUATIONS (match the customer's intent and reply in the same "
        "style; reuse the wording and the control tag closely when the situation "
        "fits, but never read the tag aloud):",
    ]
    for inst in instances:
        triggers = inst.get("triggers") or []
        example_in = triggers[0] if triggers else inst.get("intent", "")
        lines.append(f'- Customer: "{example_in}"  ->  Aarya: "{inst["response"]}"')
    return "\n".join(lines)


if __name__ == "__main__":
    # Quick manual check: python knowledge/rag.py
    print(f"Loaded knowledge keys: {list(KNOWLEDGE.keys())}")
    print(build_examples_block("grooming", 1))
