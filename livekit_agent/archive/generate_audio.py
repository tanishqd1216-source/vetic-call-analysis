import csv
import io
from gtts import gTTS

def main():
    print("Reading transcript from transcript_test_results.csv...")
    with open("transcript_test_results.csv", "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        row = next(reader)
        transcript = row["Transcript"]
    
    lines = transcript.split('\n')
    
    with open("conversation.mp3", "wb") as outfile:
        for idx, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue
                
            print(f"Processing turn {idx + 1}: {line[:50]}...")
            
            if line.startswith("AARYA:"):
                text = line.replace("AARYA:", "").strip()
                # Use Indian English accent for Aarya
                tts = gTTS(text=text, lang="en", tld="co.in")
                tts.write_to_fp(outfile)
            elif line.startswith("USER (From PDF):"):
                text = line.replace("USER (From PDF):", "").strip()
                # Use US English accent for the User (to distinguish)
                tts = gTTS(text=text, lang="en", tld="us")
                tts.write_to_fp(outfile)
            else:
                pass # skip metadata lines if any

    print("Successfully generated conversation.mp3!")

if __name__ == "__main__":
    main()
