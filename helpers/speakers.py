"""Voices from the stored embeddings: one vector per diarization label, compared with the named
speakers' samples (speaker_samples). Vectors are L2-normalised, so a dot product is a cosine
similarity.

A label's voice only uses its clean turns: at least MIN_TURN_MS long (the error rate of ECAPA
embeddings rises sharply under about 3 s) and with at most MAX_OVERLAP of overlapped speech (those
frames mix two voices).
"""

from dataclasses import dataclass

import numpy as np

import helpers.database as db

MIN_TURN_MS = 3000
MAX_OVERLAP = 0.1  # fraction of the turn


@dataclass
class Voice:
    vector: np.ndarray  # L2-normalised mean of the embeddings, weighted by duration
    seconds: float  # how much speech it comes from
    turns: int  # how many turns or samples


def is_clean(turn: db.Turn) -> bool:
    duration = turn.end_ms - turn.start_ms
    return duration >= MIN_TURN_MS and turn.overlap_ms <= MAX_OVERLAP * duration


def to_vector(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


def mean_voice(vectors: list[np.ndarray], durations_ms: list[int]) -> Voice:
    mean = np.average(np.stack(vectors), axis=0, weights=durations_ms)
    return Voice(mean / max(float(np.linalg.norm(mean)), 1e-12), sum(durations_ms) / 1000, len(vectors))


def default_model(db_conn) -> str:
    """The only embedding model of the stored turns; exits when there is none or several."""
    models = db.get_turn_embedding_models(db_conn)
    if len(models) != 1:
        raise SystemExit(
            "No turn embeddings: run pipeline/embed_speaker_turns.py first" if not models
            else f"Turn embeddings of several models {models}: choose one with --model"
        )
    return models[0]


def label_voices(db_conn, diarization_id: int, model: str) -> dict[str, Voice]:
    """The voice of each label of a diarization that has clean, embedded turns."""
    by_label = {}
    for turn, blob in db.get_turn_embeddings(db_conn, diarization_id, model):
        if blob is not None and is_clean(turn):
            vectors, durations = by_label.setdefault(turn.speaker, ([], []))
            vectors.append(to_vector(blob))
            durations.append(turn.end_ms - turn.start_ms)
    return {label: mean_voice(*samples) for label, samples in by_label.items()}


def speaker_voices(db_conn, model: str, exclude_content_id: int | None = None) -> dict[int, tuple[str, Voice]]:
    """{speaker id: (name, voice)} from the samples of every named speaker, leaving out the samples
    taken from `exclude_content_id` (to evaluate the identification of that content fairly)."""
    by_speaker = {}
    for speaker_id, name, content_id, start_ms, end_ms, blob in db.get_speaker_samples(db_conn, model):
        if content_id != exclude_content_id:
            _, vectors, durations = by_speaker.setdefault(speaker_id, (name, [], []))
            vectors.append(to_vector(blob))
            durations.append(end_ms - start_ms)
    return {speaker_id: (name, mean_voice(vectors, durations)) for speaker_id, (name, vectors, durations) in by_speaker.items()}


def best_match(voice: Voice, speakers: dict[int, tuple[str, Voice]]) -> tuple[int | None, str | None, float | None]:
    """(speaker id, name, similarity) of the named speaker closest to a voice; Nones without speakers."""
    best = (None, None, None)
    for speaker_id, (name, speaker_voice) in speakers.items():
        similarity = float(np.dot(voice.vector, speaker_voice.vector))
        if best[2] is None or similarity > best[2]:
            best = (speaker_id, name, similarity)
    return best


def describe_label(labels: dict, label: str) -> str:
    """Who a label is, from db.get_speaker_labels(): "Jean Dupont (human)", "Jean Dupont (auto, 0.92)",
    "unknown (auto, 0.41)", or "" when it was never identified."""
    if label not in labels:
        return ""
    _, name, source, similarity = labels[label]
    if source == "human":
        return f"{name or 'nobody known'} (human)"
    return f"{name or 'unknown'} (auto, {similarity:.2f})" if similarity is not None else f"{name or 'unknown'} (auto)"
