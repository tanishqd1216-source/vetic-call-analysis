# Eval fixture DB

`eval_fixture.db` (built by `build_fixture_db.py`) contains 8 **synthetic** customer
records used only by the eval harness (`tests/eval/run_eval.py`) for inbound
identity-lookup golden cases. None of this is real Vetic customer data — names,
pets, phone numbers, and appointment details are all made up for testing.

The harness points `TICKET_DB_PATH` at this file inside its own process only,
before importing `vetic_db` — it never touches the real `.env` or the production
agent process's database configuration.

Regenerate with:

```
python build_fixture_db.py
```
