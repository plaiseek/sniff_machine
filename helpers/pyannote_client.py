from pathlib import Path

import json
import requests
import sys


def assert_pyannote_servers(addresses: list):
    for address in addresses:
        url = f"http://{address}/health"
        print(f"Checking {url}...")
        try:
            r = requests.get(url, timeout=5)
            r.raise_for_status()
        except requests.exceptions.ReadTimeout:
            # The server's only worker is busy (or still loading the model): requests will wait for it
            print(f"Pyannote server {address} is busy or still loading.")
        except requests.exceptions.RequestException as e:
            sys.exit(f"Pyannote server {address} is not ready: {e}")


def wav_to_pyannote_result(
    pyannote_address: str, wav_path: Path, params: dict | None = None
) -> dict:
    """Diarize a 16 kHz mono wav. Returns {"diarization": {speaker: [[start, end], ...]},
    "exclusive_diarization": {...}} (exclusive = at most one speaker at a time).
    params: optional "num_speakers", "min_speakers", "max_speakers" (ints); by default
    pyannote estimates the number of speakers."""
    with open(wav_path, "rb") as wav_file:
        files = {"file": (wav_path.name, wav_file, "audio/wav")}
        pyannote_url = f"http://{pyannote_address}/diarize"
        response = requests.post(
            pyannote_url,
            files=files,
            data={"params": json.dumps(params or {})},
            timeout=(10, None),
        )
    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        raise requests.exceptions.HTTPError(
            f"{e}\nServer said: {response.text}", response=response
        ) from None
    return response.json()
