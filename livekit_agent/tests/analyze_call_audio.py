"""Automated stand-in for "listen to the recording and judge audio quality."

Decodes any input the project already produces (mp3 from webcall_server.py's
/recording pipeline, or a WAV from capture_call_audio.py) to mono PCM via the
venv's bundled ffmpeg (imageio_ffmpeg — same binary webcall_server.py already
uses for mp3 conversion), then flags four artifact classes against the raw
samples: clipping, clicks/discontinuities, dropouts, and pitch jumps. Each
flag carries a timestamp so it can be pointed at and re-checked, rather than
asserted from memory.

Usage:
    python analyze_call_audio.py <path/to/file.mp3-or-wav> [--json]
"""
import json
import subprocess
import sys

import numpy as np

try:
    import imageio_ffmpeg
    FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FFMPEG = "ffmpeg"

ANALYSIS_SR = 22050  # matches SARVAM_SAMPLE_RATE default; consistent unit for all checks


def load_pcm(path: str, sr: int = ANALYSIS_SR) -> np.ndarray:
    """Decode to mono 16-bit PCM at `sr` Hz via ffmpeg, return as int16 numpy array."""
    proc = subprocess.run(
        [FFMPEG, "-v", "error", "-i", path, "-f", "s16le", "-acodec", "pcm_s16le",
         "-ac", "1", "-ar", str(sr), "-"],
        capture_output=True, check=True,
    )
    return np.frombuffer(proc.stdout, dtype=np.int16)


def _ts(sample_idx: int, sr: int = ANALYSIS_SR) -> float:
    return round(sample_idx / sr, 3)


def find_clipping(samples: np.ndarray, sr: int = ANALYSIS_SR, threshold: int = 32700,
                   min_run: int = 3):
    """Sustained runs of samples pinned near full scale -> clipping/gain-staging issue."""
    clipped = np.abs(samples.astype(np.int32)) >= threshold
    flags = []
    run_start = None
    for i, c in enumerate(clipped):
        if c and run_start is None:
            run_start = i
        elif not c and run_start is not None:
            if i - run_start >= min_run:
                flags.append({"type": "clipping", "t_start": _ts(run_start, sr),
                              "t_end": _ts(i, sr), "samples": i - run_start})
            run_start = None
    if run_start is not None and len(clipped) - run_start >= min_run:
        flags.append({"type": "clipping", "t_start": _ts(run_start, sr),
                      "t_end": _ts(len(clipped), sr), "samples": len(clipped) - run_start})
    return flags


def find_clicks(samples: np.ndarray, sr: int = ANALYSIS_SR, block_ms: int = 50,
                 k: float = 15.0):
    """Sample-to-sample jumps that are large outliers vs. their block's own jump
    distribution -- the numeric signature of a chunk-boundary click or a decoder-
    warm-up crackle. Vectorized per-block (not per-sample) so it stays fast on
    real call-length audio."""
    diff = np.diff(samples.astype(np.int32))
    block_len = max(int(sr * block_ms / 1000), 8)
    n = len(diff)
    flags = []
    last_flag_end = -10 ** 9
    min_gap = int(sr * 0.01)
    for start in range(0, n, block_len):
        end = min(start + block_len, n)
        block = diff[start:end]
        mad = np.median(np.abs(block - np.median(block))) + 1e-6
        thresh = max(k * mad * 1.4826, 1500)  # MAD->robust-sigma; floor for near-silent blocks
        outliers = np.where(np.abs(block) > thresh)[0]
        for off in outliers:
            idx = start + off
            if idx - last_flag_end > min_gap:
                flags.append({"type": "click", "t": _ts(idx, sr), "delta": int(diff[idx]),
                              "local_threshold": round(float(thresh), 1)})
                last_flag_end = idx
    return flags


def find_dropouts(samples: np.ndarray, sr: int = ANALYSIS_SR, frame_ms: int = 20,
                   floor_dbfs: float = -50.0, min_gap_ms: int = 150, max_gap_ms: int = 2000,
                   edge_guard_ms: int = 300):
    """Silence runs embedded inside otherwise-speech audio -- excludes natural
    leading/trailing silence and end-of-utterance pauses longer than max_gap_ms
    (those are just the turn ending, not a mid-turn dropout)."""
    frame_len = int(sr * frame_ms / 1000)
    n_frames = len(samples) // frame_len
    if n_frames == 0:
        return []
    frames = samples[: n_frames * frame_len].reshape(n_frames, frame_len).astype(np.float64)
    rms = np.sqrt(np.mean(frames ** 2, axis=1)) + 1e-9
    dbfs = 20 * np.log10(rms / 32768.0)
    is_silent = dbfs < floor_dbfs

    edge_frames = max(1, edge_guard_ms // frame_ms)
    flags = []
    run_start = None
    for i in range(n_frames):
        if is_silent[i] and run_start is None:
            run_start = i
        elif not is_silent[i] and run_start is not None:
            run_len_ms = (i - run_start) * frame_ms
            in_body = run_start >= edge_frames and i <= n_frames - edge_frames
            has_speech_before = run_start > 0 and not is_silent[max(0, run_start - 3):run_start].all() if run_start >= 3 else run_start > 0
            if in_body and min_gap_ms <= run_len_ms <= max_gap_ms:
                flags.append({"type": "dropout", "t_start": _ts(run_start * frame_len, sr),
                              "t_end": _ts(i * frame_len, sr), "duration_ms": run_len_ms})
            run_start = None
    return flags


def _autocorr_f0(frame: np.ndarray, sr: int, fmin: float = 80.0, fmax: float = 400.0):
    """Returns None for unvoiced/silent/ambiguous frames -- plosives and fricatives
    have no fundamental, and reporting a fake F0 for them is the #1 source of
    false-positive "pitch jumps" in naive autocorrelation pitch trackers."""
    frame = frame.astype(np.float64)
    frame = frame - frame.mean()
    if np.abs(frame).max() < 400:  # effectively silent/near-silent frame
        return None
    corr = np.correlate(frame, frame, mode="full")[len(frame) - 1:]
    if corr[0] <= 0:
        return None
    corr = corr / corr[0]
    lag_min, lag_max = int(sr / fmax), int(sr / fmin)
    lag_max = min(lag_max, len(corr) - 1)
    if lag_max <= lag_min + 2:
        return None
    seg = corr[lag_min:lag_max]
    peak_off = np.argmax(seg)
    peak = peak_off + lag_min
    # A true pitch peak sits inside the search range, not pinned to either edge --
    # edge peaks are almost always unvoiced noise with no real periodicity.
    if peak_off == 0 or peak_off >= len(seg) - 1:
        return None
    if corr[peak] < 0.55:  # not periodic enough to trust as voiced
        return None
    return sr / peak


def find_pitch_jumps(samples: np.ndarray, sr: int = ANALYSIS_SR, frame_ms: int = 50,
                      hop_ms: int = 25, jump_ratio: float = 0.30, persist_frames: int = 2):
    """Track F0 across voiced frames (median-smoothed to absorb normal jitter/
    octave noise), then flag abrupt jumps that persist for >= persist_frames --
    the signature of a resample-rate or timestretch glitch, as opposed to a
    single noisy frame. Normal prosody moves F0 gradually; it doesn't jump ratio
    and then hold there within 25ms."""
    frame_len = int(sr * frame_ms / 1000)
    hop_len = int(sr * hop_ms / 1000)
    raw = []
    i = 0
    while i + frame_len <= len(samples):
        raw.append((i, _autocorr_f0(samples[i:i + frame_len], sr)))
        i += hop_len

    voiced = [(idx, f0) for idx, f0 in raw if f0 is not None]
    if len(voiced) < 3:
        return []

    # 3-tap median smoothing over the voiced sequence to absorb single-frame octave errors
    f0_vals = [f0 for _, f0 in voiced]
    smoothed = list(f0_vals)
    for j in range(1, len(f0_vals) - 1):
        smoothed[j] = float(np.median(f0_vals[j - 1:j + 2]))

    flags = []
    j = 1
    while j < len(smoothed):
        ratio = abs(smoothed[j] - smoothed[j - 1]) / smoothed[j - 1]
        if ratio > jump_ratio:
            end = min(j + persist_frames, len(smoothed))
            held = all(abs(smoothed[m] - smoothed[j]) / smoothed[j] < jump_ratio / 2
                       for m in range(j, end))
            if held and end - j >= min(persist_frames, len(smoothed) - j):
                idx = voiced[j][0]
                flags.append({"type": "pitch_jump", "t": _ts(idx, sr),
                              "f0_before": round(smoothed[j - 1], 1),
                              "f0_after": round(smoothed[j], 1),
                              "relative_change": round(ratio, 3)})
                j = end
                continue
        j += 1
    return flags


def analyze(path: str) -> dict:
    samples = load_pcm(path)
    duration_s = round(len(samples) / ANALYSIS_SR, 2)
    clicks = find_clicks(samples)
    clipping = find_clipping(samples)
    dropouts = find_dropouts(samples)
    pitch_jumps = find_pitch_jumps(samples)
    all_flags = clicks + clipping + dropouts + pitch_jumps
    all_flags.sort(key=lambda f: f.get("t", f.get("t_start", 0)))
    return {
        "path": path,
        "duration_s": duration_s,
        "sample_rate_used": ANALYSIS_SR,
        "pass": len(all_flags) == 0,
        "counts": {"clicks": len(clicks), "clipping": len(clipping),
                   "dropouts": len(dropouts), "pitch_jumps": len(pitch_jumps)},
        "flags": all_flags,
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python analyze_call_audio.py <path> [--json]")
        sys.exit(1)
    result = analyze(sys.argv[1])
    if "--json" in sys.argv:
        print(json.dumps(result, indent=2))
    else:
        print(f"{result['path']}  ({result['duration_s']}s)")
        print(f"  PASS" if result["pass"] else f"  FAIL — {sum(result['counts'].values())} flag(s)")
        print(f"  counts: {result['counts']}")
        for f in result["flags"][:30]:
            print(f"    {f}")
