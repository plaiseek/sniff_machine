from pathlib import Path

import json
import requests

import helpers.database as db
from helpers.health import named_model, ready_servers
from helpers.uploads import post_wav


def assert_speechbrain_servers(addresses: list, busy_timeout_s: float = 600) -> tuple[str, list[str]]:
    """(model, the listed servers that answer): the others are left out with a warning (see
    helpers/health.py); exits if none does. The servers must all run the same model: it is part of
    what makes a turn "embedded", so it is never guessed. A server only answers /health between two
    requests (it has one thread): busy servers are waited for (up to busy_timeout_s) until one answers."""
    return ready_servers("SpeechBrain", addresses, "SPEECHBRAIN_SERVERS", named_model, busy_timeout_s)


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
    response = post_wav(f"http://{speechbrain_address}/embed", wav_path, {"params": json.dumps(params)})
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


def embed_content_turns(
    db_conn, speechbrain_address: str, content: db.Content, wav_path: Path, model: str
) -> str:
    """Embed the exclusive turns of the content's diarizations that have no `model` embedding yet,
    in one request (the wav is uploaded once), and store them in one transaction, with None for the
    turns too short to embed so that they are not sent again. `model` is what
    assert_speechbrain_servers() returned. Returns a short summary."""
    turns = db.get_unembedded_turns(db_conn, content.id, model)
    if not turns:
        return "nothing left to embed (embedded meanwhile by another script)"
    # Diarizations of one content with other params share most of their turns: each range is sent once
    ranges = list(dict.fromkeys((turn.start_ms, turn.end_ms) for turn in turns))
    result = wav_to_speechbrain_embeddings(
        speechbrain_address, wav_path, [[start / 1000, end / 1000] for start, end in ranges]
    )
    if result["model"] != model:
        raise ValueError(f"Server {speechbrain_address} runs {result['model']}, not {model}")
    if len(result["embeddings"]) != len(ranges):
        raise ValueError(f"Server {speechbrain_address} returned {len(result['embeddings'])} embeddings for {len(ranges)} turns")
    by_range = dict(zip(ranges, result["embeddings"]))
    db.add_turn_embeddings(db_conn, model, [(turn.id, by_range[turn.start_ms, turn.end_ms]) for turn in turns])
    too_short = sum(by_range[turn.start_ms, turn.end_ms] is None for turn in turns)
    return f"{len(turns) - too_short} turns embedded, {too_short} too short to embed"
