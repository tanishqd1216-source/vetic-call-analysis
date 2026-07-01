"""Customer-record lookup, ported from the Twilio server's get_user_context().
Loads the same Excel ticket DB and returns ONE customer's record formatted for
injection into the agent's system prompt."""
import os
import re
import pandas as pd

EXCEL_PATH = os.environ.get(
    "TICKET_DB_PATH", r"C:\Users\hp\Desktop\sample data\sample_ticket_summary.xlsx"
)

try:
    TICKET_DB = pd.read_excel(EXCEL_PATH)
    print(f"[db] Loaded {len(TICKET_DB)} records from {EXCEL_PATH}")
except Exception as e:  # pragma: no cover
    print(f"[db] Failed to load ticket DB ({EXCEL_PATH}): {e}")
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
            if len(target) == 10 and "mobile No." in TICKET_DB.columns:
                hits = TICKET_DB[TICKET_DB["mobile No."].map(_phone_digits) == target]
                if not hits.empty:
                    row = hits.iloc[0]
            if row is None:
                for col in ("Ticket ID", "user_ids"):
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
            ("Owner name", g("Full name")),
            ("Pet name", g("Pet name")),
            ("Service / appointment type", g("Final App type", "Appt_type", "Type")),
            ("Appointment date", g("appointment_date")),
            ("Clinic", g("final clinic Name")),
            ("City", g("City")),
        ]:
            if val:
                parts.append(f"- {label}: {val}")
        return "\n".join(parts)
    except Exception as e:  # pragma: no cover
        print(f"[db] Error fetching user context: {e}")
        return ""


def owner_first_name(user_context: str) -> str:
    for line in (user_context or "").split("\n"):
        if line.startswith("- Owner name:"):
            name = line.split(":", 1)[1].strip()
            return name.split()[0] if name else ""
    return ""
