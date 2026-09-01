import pandas as pd
import sqlite3
import json
import os
import re

# Paths
DB_PATH = r"C:\Users\hp\Documents\sample data\vetic_data.db"
OUTPUT_JSONL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "processed_database.jsonl")

def _clean_phone(x) -> str:
    s = str(x)
    if s.endswith(".0"):
        s = s[:-2]
    d = re.sub(r"\D", "", s)
    return d[-10:] if len(d) >= 10 else d

def clean_val(val):
    """Return empty string for nan or None values to keep data clean."""
    if pd.isna(val) or str(val).lower() == "nan":
        return ""
    return str(val).strip()

def process_database():
    print(f"Loading database from {DB_PATH}...")
    try:
        conn = sqlite3.connect(DB_PATH)
        df = pd.read_sql("SELECT * FROM ticket_summary", conn)
        conn.close()
    except Exception as e:
        print(f"Failed to load database: {e}")
        return

    documents = []
    
    for idx, row in df.iterrows():
        # 1. Extract and clean fields
        ticket_id = clean_val(row.get("ticket_id"))
        user_id = clean_val(row.get("user_ids"))
        owner_name = clean_val(row.get("full_name"))
        pet_name = clean_val(row.get("pet_name"))
        appt_date = clean_val(row.get("appointment_date"))
        clinic = clean_val(row.get("final_clinic_name"))
        city = clean_val(row.get("city"))
        app_type = clean_val(row.get("final_app_type")) or clean_val(row.get("appt_type")) or clean_val(row.get("type"))
        phone = _clean_phone(row.get("mobile_no."))
        created_time = clean_val(row.get("created_time"))

        # 2. Generate Metadata
        metadata = {
            "ticket_id": ticket_id,
            "user_id": user_id,
            "owner_name": owner_name,
            "pet_name": pet_name,
            "appointment_date": appt_date,
            "clinic_name": clinic,
            "city": city,
            "appointment_type": app_type,
            "mobile_no": phone
        }
        
        # Remove empty metadata fields so we don't clutter the vector DB
        metadata = {k: v for k, v in metadata.items() if v}

        # 3. Generate Semantic Summary (The Chunk Content)
        # This is the string that gets embedded for similarity search
        summary_parts = []
        if owner_name:
            summary_parts.append(f"Owner {owner_name}")
        if pet_name:
            summary_parts.append(f"has a pet named {pet_name}.")
            
        if app_type:
            summary_parts.append(f"They booked a {app_type} appointment")
        else:
            summary_parts.append("They booked an appointment")
            
        if appt_date:
            summary_parts.append(f"for {appt_date}")
            
        if clinic and city:
            summary_parts.append(f"at the {clinic} clinic in {city}.")
        elif clinic:
            summary_parts.append(f"at the {clinic} clinic.")
            
        if ticket_id:
            summary_parts.append(f"(Ticket ID: {ticket_id})")
            
        summary_chunk = " ".join(summary_parts)

        # 4. Create the final Document object
        document = {
            "page_content": summary_chunk,
            "metadata": metadata
        }
        documents.append(document)

    # 5. Save to JSONL (Standard format for Vector DB insertion)
    print(f"Processed {len(documents)} rows. Saving to {OUTPUT_JSONL}...")
    with open(OUTPUT_JSONL, "w", encoding="utf-8") as f:
        for doc in documents:
            f.write(json.dumps(doc) + "\n")
            
    print("Done! The data is now ready to be loaded into a Vector DB or LangChain.")

if __name__ == "__main__":
    process_database()
