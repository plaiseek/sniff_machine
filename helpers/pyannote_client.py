from bisect import bisect_right
from pathlib import Path

import json
import requests
import sys

import helpers.database as db

# What servers built before Phase 4 run (their answers do not name the model)
DEFAULT_MODEL = "pyannote/speaker-diarization-community-1"


def assert_pyannote_servers(addresses: list) -> str:
    """Exit if a server is down. Returns the model the servers run (they must all run the same)."""
    models = set()
    for address in addresses:
        url = f"http://{address}/health"
        print(f"Checking {url}...")
        try:
            r = requests.get(url, timeout=5)
            r.raise_for_status()
            models.add(r.json().get("model") or DEFAULT_MODEL)
        except requests.exceptions.ReadTimeout:
            # The server's only worker is busy (or still loading the model): requests will wait for it
            print(f"Pyannote server {address} is busy or still loading.")
        except (requests.exceptions.RequestException, ValueError) as e:
            sys.exit(f"Pyannote server {address} is not ready: {e}")
    if len(models) > 1:
        sys.exit(f"The Pyannote servers run different models: {sorted(models)}")
    if not models:
        print(f"No Pyannote server answered: assuming they run {DEFAULT_MODEL}.")
    return models.pop() if models else DEFAULT_MODEL


def wav_to_pyannote_result(
    pyannote_address: str, wav_path: Path, params: dict | None = None
) -> dict:
    """Diarize a 16 kHz mono wav. Returns {"diarization": {speaker: [[start, end], ...]},
    "exclusive_diarization": {...}} (exclusive = at most one speaker at a time), plus "model" and
    "speaker_embeddings" ({speaker: [float, ...] or None}) from servers built since Phase 4.
    params: optional "num_speakers", "min_speakers", "max_speakers" (ints); by default
    pyannote estimates the number of speakers."""
    return json.loads(_post(pyannote_address, wav_path, params))


def _post(pyannote_address: str, wav_path: Path, params: dict | None) -> str:
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
    return response.text


def _merge(intervals: list) -> list:
    """Union of [start, end) intervals, as sorted disjoint intervals."""
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def _overlapped(regions: list, starts: list, start: int, end: int) -> int:
    """Length of [start, end) covered by the sorted disjoint `regions` (whose starts are `starts`)."""
    total = 0
    for region_start, region_end in regions[max(bisect_right(starts, start) - 1, 0):]:
        if region_start >= end:
            break
        total += max(0, min(end, region_end) - max(start, region_start))
    return total


def pyannote_result_to_turns(result: dict) -> list[tuple[str, int, int, int, int]]:
    """The turns to store for a server answer: (speaker, start_ms, end_ms, exclusive, overlap_ms),
    regular turns (exclusive=0) then exclusive ones (exclusive=1), by start time. overlap_ms is the
    part of the turn where at least two speakers talk at once (in the regular diarization). Turns
    shorter than 1 ms once rounded are dropped."""
    turns = []
    for exclusive, key in ((0, "diarization"), (1, "exclusive_diarization")):
        for speaker, ranges in result[key].items():
            for start, end in ranges:
                start_ms, end_ms = round(start * 1000), round(end * 1000)
                if end_ms > start_ms:
                    turns.append((exclusive, start_ms, end_ms, speaker))
    turns.sort()

    # Overlapped speech: where two distinct speakers are active (each speaker's turns merged first,
    # so that a speaker's own touching or overlapping turns never count)
    events = []
    by_speaker = {}
    for exclusive, start_ms, end_ms, speaker in turns:
        if not exclusive:
            by_speaker.setdefault(speaker, []).append((start_ms, end_ms))
    for intervals in by_speaker.values():
        for start, end in _merge(intervals):
            events += [(start, 1), (end, -1)]
    regions, active, region_start = [], 0, None
    for t, delta in sorted(events):  # at equal times, ends (-1) come before starts (+1)
        active += delta
        if active >= 2 and region_start is None:
            region_start = t
        elif active < 2 and region_start is not None:
            if t > region_start:
                regions.append([region_start, t])
            region_start = None
    regions = _merge(regions)
    starts = [start for start, _ in regions]

    return [
        (speaker, start_ms, end_ms, exclusive, _overlapped(regions, starts, start_ms, end_ms))
        for exclusive, start_ms, end_ms, speaker in turns
    ]


def diarize_content(
    db_conn,
    pyannote_address: str,
    content: db.Content,
    wav_path: Path,
    model: str,
    params: dict | None = None,
) -> str:
    """Diarize a content's wav and store the run, its turns and the raw answer (cache/pyannote_result/)
    in the database. `model` is what assert_pyannote_servers() returned. Returns a short summary."""
    result_text = _post(pyannote_address, wav_path, params)
    result = json.loads(result_text)
    if result.get("model", model) != model:
        raise ValueError(f"Server {pyannote_address} runs {result['model']}, not {model}")
    turns = pyannote_result_to_turns(result)
    diarization_id = db.add_diarization(
        db_conn,
        content.id,
        model,
        params,
        turns,
        result_text,
        Path(f"cache/pyannote_result/{content.platform}_{content.external_id}.json"),
    )
    if diarization_id is None:
        return "already stored meanwhile (by another script)"
    speakers = {speaker for speaker, _, _, exclusive, _ in turns if exclusive}
    n = sum(1 for turn in turns if turn[3])
    return f"{len(speakers)} speakers, {n} exclusive turns (diarization {diarization_id})"
