"""Customer-record lookup, ported from the Twilio server's get_user_context().
Loads the same Excel ticket DB and returns ONE customer's record formatted for
injection into the agent's system prompt."""
import os
import re
import pandas as pd
import sqlite3

_HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get(
    "TICKET_DB_PATH", os.path.join(_HERE, "data", "vetic_data.db")
)

try:
    conn = sqlite3.connect(DB_PATH)
    TICKET_DB = pd.read_sql("SELECT * FROM ticket_summary", conn)
    conn.close()
    print(f"[db] Loaded {len(TICKET_DB)} records from {DB_PATH}")
except Exception as e:  # pragma: no cover
    print(f"[db] Failed to load ticket DB ({DB_PATH}): {e}")
    TICKET_DB = None


def _phone_digits(x) -> str:
    s = str(x)
    if s.endswith(".0"):
        s = s[:-2]
    d = re.sub(r"\D", "", s)
    return d[-10:] if len(d) >= 10 else d


def get_user_context(identifier=0) -> str:
    """Return ONE customer's record as a formatted, prompt-ready string.
    identifier: int row index, 10-digit phone, or a Ticket ID / user_id string."""
    if TICKET_DB is None or TICKET_DB.empty:
        return ""
    try:
        row = None
        if isinstance(identifier, int):
            if 0 <= identifier < len(TICKET_DB):
                row = TICKET_DB.iloc[identifier]
        else:
            ident = str(identifier).strip()
            target = _phone_digits(ident)
            if len(target) == 10 and "mobile_no." in TICKET_DB.columns:
                hits = TICKET_DB[TICKET_DB["mobile_no."].map(_phone_digits) == target]
                if not hits.empty:
                    row = hits.iloc[0]
            if row is None:
                for col in ("ticket_id", "user_ids"):
                    if col in TICKET_DB.columns:
                        hits = TICKET_DB[TICKET_DB[col].astype(str).str.strip() == ident]
                        if not hits.empty:
                            row = hits.iloc[0]
                            break
        if row is None:
            return ""

        def g(*keys):
            for k in keys:
                if k in row:
                    v = str(row[k]).strip()
                    if v and v.lower() != "nan":
                        return v
            return None

        parts = [
            "CUSTOMER RECORD ON FILE (the person you are calling).",
            "VERIFY each detail with the customer in your own words; do NOT assume it is correct until they confirm:",
        ]
        for label, val in [
            ("Owner name", g("full_name")),
            ("Pet name", g("pet_name")),
            ("Last service / appointment type", g("final_app_type", "appt_type", "type")),
            ("Last appointment date", g("appointment_date")),
            ("Clinic", g("final_clinic_name")),
            ("City", g("city")),
        ]:
            if val:
                parts.append(f"- {label}: {val}")
        return "\n".join(parts)
    except Exception as e:  # pragma: no cover
        print(f"[db] Error fetching user context: {e}")
        return ""

def get_user_raw(identifier=0) -> dict:
    """Return ONE customer's record as a raw dictionary."""
    if TICKET_DB is None or TICKET_DB.empty:
        return {}
    try:
        row = None
        if isinstance(identifier, int):
            if 0 <= identifier < len(TICKET_DB):
                row = TICKET_DB.iloc[identifier]
        else:
            ident = str(identifier).strip()
            target = _phone_digits(ident)
            if len(target) == 10 and "mobile_no." in TICKET_DB.columns:
                hits = TICKET_DB[TICKET_DB["mobile_no."].map(_phone_digits) == target]
                if not hits.empty:
                    row = hits.iloc[0]
            if row is None:
                for col in ("ticket_id", "user_ids"):
                    if col in TICKET_DB.columns:
                        hits = TICKET_DB[TICKET_DB[col].astype(str).str.strip() == ident]
                        if not hits.empty:
                            row = hits.iloc[0]
                            break
        if row is None:
            return {}
        # Convert all values to standard Python strings for safe JSON serialization
        row_dict = row.fillna("").to_dict()
        return {str(k): str(v) for k, v in row_dict.items()}
    except Exception as e:
        print(f"[db] Error fetching user raw data: {e}")
        return {}


_NAME_TITLES = {"mr", "mrs", "ms", "miss", "dr", "master", "shri", "smt"}


def owner_first_name(user_context: str) -> str:
    for line in (user_context or "").split("\n"):
        if line.startswith("- Owner name:"):
            name = line.split(":", 1)[1].strip()
            parts = name.split()
            for part in parts:
                if part.strip(".").lower() not in _NAME_TITLES:
                    return part
            return parts[0] if parts else ""
    return ""
