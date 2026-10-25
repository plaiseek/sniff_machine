"""Check the model servers listed in servers.py.

    python check_servers.py                     # every server
    python check_servers.py whisper pyannote    # only these kinds (or steps: transcribe, diarize)

Prints one line per server: OK, BUSY (its only worker is busy, or the model is still loading:
work sent to it waits), LOADING or DOWN. Exits with 1 if a server of the requested kinds is DOWN.
"""

import sys

import requests

import servers

KINDS = {
    "whisper": servers.WHISPER_SERVERS,
    "pyannote": servers.PYANNOTE_SERVERS,
    "speechbrain": servers.SPEECHBRAIN_SERVERS,
    "wtpsplit": servers.WTPSPLIT_SERVERS,
}
STEP_KINDS = {"transcribe": "whisper", "diarize": "pyannote", "embed": "speechbrain", "split": "wtpsplit"}

kinds = [STEP_KINDS.get(arg, arg) for arg in sys.argv[1:]] or list(KINDS)
if unknown := [kind for kind in kinds if kind not in KINDS]:
    sys.exit(f"Unknown server kind(s) {unknown}: use {list(KINDS)} or {list(STEP_KINDS)}")

down = 0
for kind in dict.fromkeys(kinds):
    if not KINDS[kind]:
        print(f"{kind:12} {'-':22} DOWN (no server listed in servers.py)")
        down += 1
    for address in KINDS[kind]:
        try:
            r = requests.get(f"http://{address}/health", timeout=5)
            r.raise_for_status()
            answer = r.json()
            # Servers that answer /health while they work say so (e.g. pyannote since Phase 4)
            status = "BUSY" if answer.get("busy") else "OK"
            if model := answer.get("model"):
                status += f" ({model})"
        except requests.exceptions.ReadTimeout:
            status = "BUSY"
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 503:
                status = "LOADING"  # whisper.cpp answers 503 until its model is loaded
            else:
                status = f"DOWN (HTTP {e.response.status_code})"
                down += 1
        except (requests.exceptions.RequestException, ValueError) as e:
            status = f"DOWN ({type(e).__name__})"
            down += 1
        print(f"{kind:12} {address:22} {status}")
sys.exit(1 if down else 0)
