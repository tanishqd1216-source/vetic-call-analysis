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
import re
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


# ── Fast-path intent matching ────────────────────────────────────────────────
# On a confident match we skip BOTH Claude and TTS and play a pre-rendered clip.
# A wrong canned reply ignores what the caller actually said, which is worse than
# a slightly slower-but-correct LLM reply — so matching is deliberately strict and
# errs toward "no match" (fall through to the LLM).

CONTROL_TAGS = ("[NEXT_STAGE]", "[END_CALL]")


def _normalize(s: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", str(s).lower())).strip()


def _tokens(s: str) -> set:
    return set(_normalize(s).split())


def split_control_tag(text: str):
    """Return (clean_text_without_tag, tag_or_None). Used so the tag is never
    spoken aloud but is still available to drive the stage state machine."""
    tag = None
    for t in CONTROL_TAGS:
        if t in text:
            tag = t
            text = text.replace(t, "")
    return text.strip(), tag


def all_instances():
    """Yield (scenario, stage_name, instance) for every loaded knowledge instance.
    The server uses this to pre-render each canned response to audio once."""
    for (scenario, stage_name), instances in KNOWLEDGE.items():
        for inst in instances:
            yield scenario, stage_name, inst


def match_intent(user_speech: str, scenario: str, stage: int,
                 fallback_scenario: str = None, min_score: float = 0.72):
    """Return the best-matching instance for a confident hit, else None.

    Scoring is Jaccard token overlap (|A∩B| / |A∪B|) between the caller's speech
    and each trigger phrase. Jaccard punishes length mismatch in BOTH directions,
    so a short generic trigger ("go ahead") won't match a long, specific utterance
    ("go ahead and tell me why my dog is bleeding"). The primary scenario is tried
    first; the campaign fallback is only consulted if the primary yields nothing —
    this is what lets stage-1 turns match even before a scenario keyword appears.
    """
    stage_name = STAGE_NAMES.get(stage, "")
    speech_tokens = _tokens(user_speech)
    if not speech_tokens:
        return None

    scenarios = [scenario]
    if fallback_scenario and fallback_scenario != scenario:
        scenarios.append(fallback_scenario)

    for sc in scenarios:
        local_best, local_score = None, 0.0
        for inst in KNOWLEDGE.get((sc, stage_name), []):
            for trig in inst.get("triggers", []):
                t_tokens = _tokens(trig)
                if not t_tokens:
                    continue
                union = len(speech_tokens | t_tokens)
                score = len(speech_tokens & t_tokens) / union if union else 0.0
                if score > local_score:
                    local_best, local_score = inst, score
        if local_best and local_score >= min_score:
            return local_best
    return None


if __name__ == "__main__":
    # Quick manual check: python knowledge/rag.py
    print(f"Loaded knowledge keys: {list(KNOWLEDGE.keys())}")
    print(build_examples_block("grooming", 1))
    print("\n--- match_intent smoke test (scenario=general, fallback=grooming) ---")
    for utterance in [
        "Yes, who is this?",          # -> groom-intro-01
        "go ahead",                   # -> groom-intro-04
        "go ahead and tell me why my dog is bleeding from the neck",  # -> no match
        "what's the weather like",    # -> no match
        "call me later",              # -> groom-intro-03 ([END_CALL])
    ]:
        hit = match_intent(utterance, "general", 1, fallback_scenario="grooming")
        print(f"  {utterance!r:65} -> {hit['id'] if hit else None}")
