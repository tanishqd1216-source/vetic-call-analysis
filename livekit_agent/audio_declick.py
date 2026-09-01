"""Post-processing / TTS-invocation helpers for the Vetic voice pipeline.

Two distinct artifacts were measured on captured agent-only audio (no
barge-in, no caller speech involved -- 2026-07-29 diagnostic session):

1. Clicks at TTS segment boundaries. livekit-plugins-sarvam's SynthesizeStream
   tokenizes text into per-sentence segments and streams each one back over
   its own leg of the Sarvam TTS websocket (see `SynthesizeStream._segments_ch`
   / `_tokenize_input()` in that plugin) -- adjacent segments don't always
   seam sample-continuously. `declick()` smooths these.
2. Dropouts (silence gaps up to ~1.1s) mid-utterance. `SynthesizeStream.
   _process_segments()` processes sentence segments strictly one at a time --
   `async for word_stream in self._segments_ch: await self._run_ws(word_stream,
   ...)` -- so sentence N+1's request/response round-trip only starts after
   sentence N fully finishes. If playback drains sentence N's buffered audio
   before sentence N+1's first bytes arrive, that gap is audible. `speak_one_shot()`
   avoids this by requesting the *entire* utterance as a single non-streaming
   synthesis call (`TTS.synthesize()` / ChunkedStream) instead of letting the
   framework split it into sequential per-sentence streamed segments -- one
   continuous audio buffer, no inter-segment gap possible. Tradeoff: for a
   multi-sentence LLM-generated reply, this waits for the full text before any
   audio starts, instead of speaking the first sentence while later ones are
   still being generated.

Both wrap `Agent.default.tts_node()` / call the TTS plugin's public API
(the documented extension points for this) rather than patching the vendored
plugin, so they survive plugin upgrades.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterable

import numpy as np
from livekit import rtc

# A same-speaker waveform can legitimately move a few hundred units between
# adjacent samples (loud consonants, etc). The clicks we measured were jumps
# of ~1500-16000 at frame boundaries with no such content on either side --
# comfortably clear of natural sample-to-sample movement.
JUMP_THRESHOLD = 1200
RAMP_SAMPLES = 8


def _smooth_jump(data: np.ndarray, idx: int, n: int) -> None:
    """Replace the hard step at data[idx] with a short linear ramp spanning
    idx-1 .. idx+n, in place. Mirrors analyze_call_audio.py's click detection
    (a single-sample delta that's a large outlier vs. its local neighborhood)
    so the fix targets exactly what was measured, not a looser approximation."""
    lo = max(idx - 1, 0)
    hi = min(idx + n, len(data) - 1)
    if hi <= lo:
        return
    ramp = np.linspace(data[lo], data[hi], hi - lo + 1)
    data[lo : hi + 1] = ramp.astype(np.int16)


def _declick_frame(data: np.ndarray) -> None:
    """In-place: smooth any single-sample jump inside this frame that's a
    strong outlier vs. the frame's own jump distribution."""
    if len(data) < 8:
        return
    diff = np.diff(data.astype(np.int32))
    mad = np.median(np.abs(diff - np.median(diff))) + 1e-6
    thresh = max(15.0 * mad * 1.4826, JUMP_THRESHOLD)
    outliers = np.where(np.abs(diff) > thresh)[0]
    for off in outliers:
        _smooth_jump(data, int(off) + 1, RAMP_SAMPLES)


async def declick(frames: AsyncIterable[rtc.AudioFrame]) -> AsyncGenerator[rtc.AudioFrame, None]:
    prev_last_sample: int | None = None
    async for frame in frames:
        data = np.frombuffer(frame.data, dtype=np.int16).copy()
        if len(data) == 0:
            yield frame
            continue

        if prev_last_sample is not None:
            jump = int(data[0]) - prev_last_sample
            if abs(jump) > JUMP_THRESHOLD:
                n = min(RAMP_SAMPLES, len(data) - 1)
                if n > 0:
                    ramp = np.linspace(prev_last_sample, int(data[n]), n + 1)
                    data[: n + 1] = ramp.astype(np.int16)

        _declick_frame(data)

        prev_last_sample = int(data[-1])
        yield rtc.AudioFrame(
            data=data.tobytes(),
            sample_rate=frame.sample_rate,
            num_channels=frame.num_channels,
            samples_per_channel=frame.samples_per_channel,
        )


async def speak_one_shot(agent, text: AsyncIterable[str]) -> AsyncGenerator[rtc.AudioFrame, None]:
    """Join the full utterance text and synthesize it as a single non-streaming
    request (TTS.synthesize / ChunkedStream) instead of the framework's default
    per-sentence streamed segments -- see module docstring, artifact #2."""
    activity = agent._get_activity_or_raise()
    if activity.tts is None:
        raise RuntimeError("tts_node called but no TTS is configured")

    full_text = "".join([chunk async for chunk in text])
    if not full_text.strip():
        return

    conn_options = activity.session.conn_options.tts_conn_options
    async with activity.tts.synthesize(full_text, conn_options=conn_options) as stream:
        async for ev in stream:
            yield ev.frame
