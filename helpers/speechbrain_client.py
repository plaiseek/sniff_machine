from pathlib import Path

import json
import requests
import sys


def assert_speechbrain_servers(addresses: list):
    for address in addresses:
        url = f"http://{address}/health"
        print(f"Checking {url}...")
        try:
            r = requests.get(url, timeout=5)
            r.raise_for_status()
        except requests.exceptions.ReadTimeout:
            # The server's only worker is busy (or still loading the model): requests will wait for it
            print(f"SpeechBrain server {address} is busy or still loading.")
        except requests.exceptions.RequestException as e:
            sys.exit(f"SpeechBrain server {address} is not ready: {e}")


def wav_to_speechbrain_embeddings(
    speechbrain_address: str,
    wav_path: Path,
    segments: list,
    min_duration: float = 1.0,
    max_duration: float = 30.0,
    batch_size: int = 16,
) -> dict:
    """One L2-normalised voice embedding per [start, end] segment (seconds) of a 16 kHz mono wav.
    Returns {"model", "dimension", "embeddings": [list[float] | None, ...]} in segment order:
    None for a segment shorter than min_duration (once clipped to the audio duration).
    Segments longer than max_duration are embedded in chunks whose embeddings are averaged."""
    params = {
        "segments": [[float(start), float(end)] for start, end in segments],
        "min_duration": float(min_duration),
        "max_duration": float(max_duration),
        "batch_size": int(batch_size),
    }
    with open(wav_path, "rb") as wav_file:
        files = {"file": (wav_path.name, wav_file, "audio/wav")}
        speechbrain_url = f"http://{speechbrain_address}/embed"
        response = requests.post(
            speechbrain_url,
            files=files,
            data={"params": json.dumps(params)},
            timeout=(10, None),
        )
    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        raise requests.exceptions.HTTPError(
            f"{e}\nServer said: {response.text}", response=response
        ) from None
    return response.json()


def diarization_to_speechbrain_embeddings(
    speechbrain_address: str, wav_path: Path, diarization: dict, **kwargs
) -> dict:
    """Embed every turn of a pyannote_client diarization ({speaker: [[start, end], ...]}), preferably
    its "exclusive_diarization" so that no turn mixes two voices. kwargs: see wav_to_speechbrain_embeddings.
    Returns {"model", "dimension", "turns": [{"speaker", "start", "end", "embedding"}, ...]} sorted by start."""
    turns = sorted(
        (start, end, speaker)
        for speaker, ranges in diarization.items()
        for start, end in ranges
    )
    result = wav_to_speechbrain_embeddings(
        speechbrain_address, wav_path, [[start, end] for start, end, _ in turns], **kwargs
    )
    return {
        "model": result["model"],
        "dimension": result["dimension"],
        "turns": [
            {"speaker": speaker, "start": start, "end": end, "embedding": embedding}
            for (start, end, speaker), embedding in zip(turns, result["embeddings"])
        ],
    }
