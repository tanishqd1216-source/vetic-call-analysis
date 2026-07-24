"""Container entrypoint — picks which agent variant to run via AGENT_ENTRYPOINT.

AGENT_ENTRYPOINT=inbound  -> agent_inbound.py (identity-first inbound flow)
AGENT_ENTRYPOINT=outbound -> agent.py         (pre-loaded outbound flow)

Only one can run at a time (see webcall_server.py's agent_process comment: both
scripts register under the same LiveKit agent name, so dispatch is ambiguous if
two workers are live at once). Always runs in `start` (production) mode, never
`dev`.
"""
import os
import sys

TARGETS = {
    "inbound": "agent_inbound.py",
    "outbound": "agent.py",
}


def main() -> None:
    mode = os.environ.get("AGENT_ENTRYPOINT", "inbound").strip().lower()
    script = TARGETS.get(mode)
    if not script:
        sys.exit(
            f"Invalid AGENT_ENTRYPOINT={mode!r}; must be one of {sorted(TARGETS)}"
        )
    here = os.path.dirname(os.path.abspath(__file__))
    os.execv(sys.executable, [sys.executable, os.path.join(here, script), "start"])


if __name__ == "__main__":
    main()
