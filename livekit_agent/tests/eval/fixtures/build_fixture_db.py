"""Builds the synthetic customer-record fixture DB used ONLY by the eval harness
(tests/eval/run_eval.py) for inbound identity-lookup golden cases.

Schema matches exactly what vetic_db.py's TICKET_DB expects (table `ticket_summary`,
including the literal `mobile_no.` column name with its trailing period) so
get_user_context()/get_user_raw() work unmodified against this file.

All rows below are obviously synthetic test data, not real Vetic customers — see
README.md in this directory. Run directly to (re)build eval_fixture.db:

    python build_fixture_db.py
"""
import os
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "eval_fixture.db")

COLUMNS = [
    "ticket_id", "user_ids", "mobile_no.", "full_name", "pet_name",
    "final_app_type", "appointment_date", "final_clinic_name", "city",
]

# (ticket_id, user_ids, mobile_no., full_name, pet_name, final_app_type,
#  appointment_date, final_clinic_name, city)
ROWS = [
    ("EVAL-T001", "eval-u001", "9000000001", "Fixture Customer One", "Testbud",
     "Grooming", "2026-08-01", "Test Clinic - HSR Layout", "Bengaluru"),
    ("EVAL-T002", "eval-u002", "9000000002", "Fixture Customer Two", "Buddy",
     "OPD Consultation", "2026-08-03", "Test Clinic - Koramangala", "Bengaluru"),
    ("EVAL-T003", "eval-u003", "9000000003", "Fixture Customer Three", "Whiskers",
     "Home Delivery", "2026-08-05", "Test Clinic - Indiranagar", "Bengaluru"),
    ("EVAL-T004", "eval-u004", "9000000004", "Fixture Customer Four", "Rex",
     "Grooming", "2026-08-07", "Test Clinic - Whitefield", "Bengaluru"),
    ("EVAL-T005", "eval-u005", "9000000005", "Fixture Customer Five", "Milo",
     "Membership", "2026-08-09", "Test Clinic - JP Nagar", "Bengaluru"),
    ("EVAL-T006", "eval-u006", "9000000006", "Fixture Customer Six", "Luna",
     "OPD Consultation", "2026-08-11", "Test Clinic - Marathahalli", "Bengaluru"),
    ("EVAL-T007", "eval-u007", "9000000007", "Fixture Customer Seven", "Charlie",
     "At Home Service", "2026-08-13", "Test Clinic - HSR Layout", "Bengaluru"),
    ("EVAL-T008", "eval-u008", "9000000008", "Fixture Customer Eight", "Bella",
     "Grooming", "2026-08-15", "Test Clinic - Koramangala", "Bengaluru"),
]


def build() -> None:
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    cols_sql = ", ".join(f'"{c}" TEXT' for c in COLUMNS)
    conn.execute(f"CREATE TABLE ticket_summary ({cols_sql})")
    placeholders = ", ".join("?" for _ in COLUMNS)
    conn.executemany(f"INSERT INTO ticket_summary VALUES ({placeholders})", ROWS)
    conn.commit()
    conn.close()
    print(f"[fixture] wrote {len(ROWS)} synthetic rows to {DB_PATH}")


if __name__ == "__main__":
    build()
