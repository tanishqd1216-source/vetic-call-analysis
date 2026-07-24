import os
from livekit.plugins import sarvam

def test_tts():
    """Print the current TTS model and synthesize a short test phrase.

    The phrase includes a tricky word (quokka) to verify pronunciation.
    """
    model = os.getenv("SARVAM_TTS_MODEL", "unknown")
    print(f"Current TTS model: {model}")

    # Initialise the Sarvam TTS client with the selected model
    tts = sarvam.TTS(model=model)
    text = "Hello, my name is Priya and I love the word quokka."
    out_path = "test_tts.wav"
    try:
        tts.synthesize(text, out_path)
        print(f"Saved test audio → {out_path}")
    except Exception as e:
        print(f"❌ TTS synthesis failed: {e}")

if __name__ == "__main__":
    test_tts()
