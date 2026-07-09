from pathlib import Path

import json
import requests
import sys


def assert_pyannote_servers(addresses: list):
    for address in addresses:
        url = f"http://{address}/ready"
        print(f"Checking {url}...")
        try:
            r = requests.get(url, timeout=5)
            r.raise_for_status()
        except requests.exceptions.RequestException as e:
            print(e)
            sys.exit()


def mp3_to_pyannote_result(pyannote_address: str, mp3_path: Path) -> None:
    params = {"num_speakers": 2}
    with open(mp3_path, "rb") as mp3_file:
        files = {"file": (mp3_path.name, mp3_file, "audio/mpeg")}
        pyannote_url = f"http://{pyannote_address}/diarize"
        response = requests.post(
            pyannote_url,
            files=files,
            data={"params": json.dumps(params)},
        )
    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        raise requests.exceptions.HTTPError(
            f"{e}\nServer said: {response.text}", response=response
        ) from None
    return response.json()["diarization"]
