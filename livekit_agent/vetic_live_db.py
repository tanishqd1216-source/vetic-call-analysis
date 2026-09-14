"""Live customer-record lookup against Vetic's production user_profile Mongo store
(the same customer-360 document already used by the manual call dashboard — see its
embedded `call_data` field). Same public interface as vetic_db.py (get_user_context,
get_user_raw, owner_first_name), so agent_outbound.py / agent_inbound.py can switch
data sources with a one-line import change.

Falls back to vetic_db.py's static ticket-DB lookup whenever the live collection is
unreachable or has no match for the identifier, so behavior is unchanged until
VETIC_MONGO_URI is configured.
"""
import os
import re

import vetic_db as _fallback

try:
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError
    _PYMONGO_AVAILABLE = True
except ImportError:
    MongoClient = None
    PyMongoError = Exception
    _PYMONGO_AVAILABLE = False

MONGO_URI = os.environ.get("VETIC_MONGO_URI")
MONGO_DB = os.environ.get("VETIC_MONGO_DB", "user_profile")
MONGO_COLLECTION = os.environ.get("VETIC_MONGO_COLLECTION", "user_profile")

_collection = None
if not MONGO_URI:
    print("[vetic_live_db] VETIC_MONGO_URI not set — falling back to vetic_db (static file)")
elif not _PYMONGO_AVAILABLE:
    print("[vetic_live_db] VETIC_MONGO_URI is set but pymongo is not installed — falling back to vetic_db (static file)")
else:
    try:
        _client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
        _collection = _client[MONGO_DB][MONGO_COLLECTION]
        print(f"[vetic_live_db] Connected to {MONGO_DB}.{MONGO_COLLECTION}")
    except PyMongoError as e:  # pragma: no cover
        print(f"[vetic_live_db] Failed to connect to Mongo: {e}")
        _collection = None


def _phone_digits(x) -> str:
    s = str(x)
    if s.endswith(".0"):
        s = s[:-2]
    d = re.sub(r"\D", "", s)
    return d[-10:] if len(d) >= 10 else d


def _clean(v):
    if not isinstance(v, str):
        return v
    v = v.strip()
    return v if v and v.lower() not in ("nan", "-", "none", "nat", "") else None


def _find_doc(identifier) -> dict:
    """Look up by trailing-10-digit phone match (client or pet mobile fields cover
    both bare "8447942666" and "+918447942666" formats), else by unique_id."""
    if _collection is None:
        return None
    ident = str(identifier).strip()
    target = _phone_digits(ident)
    try:
        if len(target) == 10:
            doc = _collection.find_one({"client_profile.mobiles.mobile": {"$regex": f"{target}$"}})
            if doc:
                return doc
            doc = _collection.find_one({"pet_profiles.mobile": {"$regex": f"{target}$"}})
            if doc:
                return doc
        return _collection.find_one({"unique_id": ident})
    except PyMongoError as e:  # pragma: no cover
        print(f"[vetic_live_db] Lookup failed: {e}")
        return None


def get_user_context(identifier=0) -> str:
    """Return ONE customer's record as a formatted, prompt-ready string, same shape
    as vetic_db.get_user_context()."""
    doc = _find_doc(identifier)
    if not doc:
        return _fallback.get_user_context(identifier)
    try:
        profile = doc.get("client_profile") or {}
        pets = ((doc.get("pet_profiles") or {}).get("pet_profiles")) or []
        appts = ((doc.get("appointments") or {}).get("appointments")) or []
        dnd = (doc.get("dnd") or {}).get("dnd")

        last_appt = appts[-1] if appts else {}
        pet_name = (pets[0].get("name") if pets else None) or last_appt.get("patient_name")

        parts = [
            "CUSTOMER RECORD ON FILE (the person you are calling).",
            "VERIFY each detail with the customer in your own words; do NOT assume it is correct until they confirm:",
        ]
        for label, val in [
            ("Owner name", _clean(profile.get("name"))),
            ("Pet name", _clean(pet_name)),
            ("Last service / appointment type", _clean(last_appt.get("appointment_type"))),
            ("Last appointment date", _clean(last_appt.get("appointment_date"))),
            ("Clinic", _clean(last_appt.get("clinic_name"))),
            ("City", _clean(profile.get("city"))),
        ]:
            if val:
                parts.append(f"- {label}: {val}")
        if dnd:
            parts.append(
                "- DND: TRUE — this customer has opted out of calls. Do NOT proceed with "
                "the call; apologize briefly and end it, and flag for compliance review."
            )
        return "\n".join(parts)
    except Exception as e:  # pragma: no cover
        print(f"[vetic_live_db] Error formatting user context: {e}")
        return _fallback.get_user_context(identifier)


def get_user_raw(identifier=0) -> dict:
    """Return ONE customer's record as a raw dict, same shape as vetic_db.get_user_raw()."""
    doc = _find_doc(identifier)
    if not doc:
        return _fallback.get_user_raw(identifier)
    doc.pop("_id", None)
    return doc


owner_first_name = _fallback.owner_first_name
