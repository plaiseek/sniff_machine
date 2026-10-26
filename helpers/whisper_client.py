from pathlib import Path
from typing import NamedTuple

import json
import math
import os
import statistics

from helpers.health import ready_servers
from helpers.uploads import post_wav


def assert_whisper_servers(addresses: list) -> list[str]:
    """The listed servers that answer: the others are left out with a warning (see helpers/health.py);
    exits if none does. A server loading another model (503, during a POST /load) is kept: work sent
    to it waits. A server that is starting is down: whisper.cpp only opens its port once its model
    is loaded."""

    def model_of(answer):
        # whisper.cpp serves /health: {"status":"ok"}, and does not say which model it runs
        if answer.get("status") != "ok":
            raise ValueError(f"Unexpected Whisper.cpp response: {answer}")
        return None

    return ready_servers("Whisper", addresses, "WHISPER_SERVERS", model_of)[1]


supported_params = [
    "offset_t",
    "offset_n",
    "duration",
    "max_context",
    "max_len",
    "best_of",
    "beam_size",
    "audio_ctx",
    "word_thold",
    "entropy_thold",
    "logprob_thold",
    "no_speech_thold",
    "debug_mode",
    "translate",
    "diarize",
    "tinydiarize",
    "split_on_word",
    "no_timestamps",
    "token_timestamps",
    "language",
    "detect_language",
    "prompt",
    "carry_initial_prompt",
    "response_format",
    "temperature",
    "temperature_inc",
    "suppress_non_speech",
    "suppress_nst",
    "vad",
    "vad_threshold",
    "vad_min_speech_duration_ms",
    "vad_min_silence_duration_ms",
    "vad_max_speech_duration_s",
    "vad_speech_pad_ms",
    "vad_samples_overlap",
    "no_language_probabilities",
]


def wav_to_whisper_result(
    whisper_address: str,
    wav_path: Path,
    whisper_result_path: Path,
    language: str = "fr",
) -> None:
    data = {
        "language": language,
        "response_format": "verbose_json",
        "beam_size": "5",
        "temperature": "0.0",
        "temperature_inc": "0.4",
        "entropy_thold": "2.20",
        "logprob_thold": "-0.8",
        "no_speech_thold": "0.4",
        "max_context": "0",
        "suppress_nst": "true",
        "split_on_word": "true",
        "vad": "true",
        "vad_threshold": "0.6",
        "vad_min_silence_duration_ms": "500",
        "vad_max_speech_duration_s": "20",
        "vad_speech_pad_ms": "400",
        "max_len": "1",
    }
    for key in data.keys():
        if key not in supported_params:
            raise ValueError(f"{key} not supported !")

    response = post_wav(f"http://{whisper_address}/inference", wav_path, data)

    response.raise_for_status()

    # Written under a temporary name then renamed: never a half-written result that looks valid
    os.makedirs(whisper_result_path.parent, exist_ok=True)
    partial_path = whisper_result_path.with_name(f"{whisper_result_path.name}.{os.getpid()}.part")
    with open(partial_path, "w", encoding="utf-8") as f:
        f.write(response.text)
    os.replace(partial_path, whisper_result_path)


def dtw_to_srt_timestamp(dtw: int) -> str:
    """Convert Whisper DTW to HH:mm:ss,ms format."""
    if dtw < 0:
        raise ValueError("DTW cannot be negative.")
    total_ms = dtw * 10
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


class WhisperWord(NamedTuple):
    text: str  # with the space Whisper puts before every word: " l'histoire", " ?"
    start_ms: int  # DTW time of its first token
    end_ms: int  # DTW time of its last token: often = start_ms, sometimes before it
    prob: float | None  # product of its token probabilities (None: no token given)


# A transcription whose DTW word times are, by median, further than this from its segment times was
# read with the wrong VAD mapping (e.g. whisper.cpp changed which times it maps): its words are refused
MAX_MEDIAN_SHIFT_MS = 2000
MIN_WORDS_FOR_SHIFT_CHECK = 50


def load_whisper_result(whisper_result_path: Path) -> dict:
    with open(whisper_result_path, "r", encoding="utf-8") as f:
        return json.load(f)


def whisper_words(whisper_result: dict) -> list[WhisperWord]:
    """The words of a transcription result in order, on the timeline of the audio sent (the server
    returns one word per segment: max_len=1). The one source of word times: the database, the SRT
    export and the speaker tools use it."""
    # VAD removes parts of the audio and Whisper.cpp correct the segment start/end but not the word ones
    words_with_dtw = []
    shifts = []
    for segment in whisper_result["segments"]:
        if len(segment["text"]) == 0:
            continue
        words = segment.get("words") or []
        if len(words) > 0 and words[0]["t_dtw"] >= 0 and words[-1]["t_dtw"] >= 0:
            dtw_start = words[0]["t_dtw"] + round(100 * (segment["start"] - words[0]["start"]))
            dtw_end = words[-1]["t_dtw"] + round(100 * (segment["end"] - words[-1]["end"]))
            shifts.append(abs(dtw_start - round(100 * segment["start"])))
        else:
            # No word or no DTW timestamp (t_dtw = -1): use the segment times
            dtw_start, dtw_end = round(100 * segment["start"]), round(100 * segment["end"])
        prob = math.prod(w["probability"] for w in words) if words else None
        words_with_dtw.append(WhisperWord(segment["text"], 10 * dtw_start, 10 * dtw_end, prob))  # DTW unit: 10 ms
    # Measured on 414 transcriptions: medians of 0.1 to 0.5 s (only hallucinated music clips of a
    # few words go beyond), against tens of seconds when the VAD offset is applied twice or never
    if len(shifts) >= MIN_WORDS_FOR_SHIFT_CHECK and 10 * statistics.median(shifts) > MAX_MEDIAN_SHIFT_MS:
        raise ValueError(
            f"Word times are {10 * statistics.median(shifts) / 1000:.1f} s (median) away from the segment times:"
            " the VAD mapping of whisper.cpp may have changed (see whisper_words() and the pinned whisper.cpp commit)"
        )
    return words_with_dtw


def whisper_result_to_words(whisper_result_path: Path) -> list[WhisperWord]:
    return whisper_words(load_whisper_result(whisper_result_path))


def whisper_result_to_srt(whisper_result_path: Path, srt_path: Path):
    words = whisper_result_to_words(whisper_result_path)
    os.makedirs(srt_path.parent, exist_ok=True)
    with open(srt_path, "w", encoding="utf-8") as f:
        for i, word in enumerate(words, 1):
            f.write(f"{i}\n{dtw_to_srt_timestamp(word.start_ms // 10)} --> {dtw_to_srt_timestamp(word.end_ms // 10)}\n{word.text}\n\n")
