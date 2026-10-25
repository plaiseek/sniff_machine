from pathlib import Path

import json
import os
import requests
import sys
import time


def assert_whisper_servers(addresses: list, loading_timeout_s: float = 600):
    """Exit if a server is down. A server still loading its model is waited for (up to
    loading_timeout_s), e.g. when a script starts right after run_whisper_servers.py."""
    for address in addresses:
        # whisper.cpp serves /health: {"status":"ok"}, or 503 while the model is loading
        url = f"http://{address}/health"
        print(f"Checking {url}...")
        deadline = time.monotonic() + loading_timeout_s
        while True:
            try:
                r = requests.get(url, timeout=5)
                if r.status_code == 503 and time.monotonic() < deadline:
                    print(f"Whisper server {address} is loading its model, waiting...", flush=True)
                    time.sleep(10)
                    continue
                r.raise_for_status()
                if r.json().get("status") != "ok":
                    raise ValueError(f"Unexpected Whisper.cpp response: {r.text}")
                break
            except (requests.exceptions.RequestException, ValueError) as e:
                sys.exit(f"Whisper server {address} is not ready: {e}")


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

    with open(wav_path, "rb") as wav_file:
        files = {"file": (wav_path.name, wav_file, "audio/wav")}
        whisper_url = f"http://{whisper_address}/inference"
        response = requests.post(
            whisper_url, files=files, data=data, timeout=(10, None)
        )

    response.raise_for_status()

    os.makedirs(whisper_result_path.parent, exist_ok=True)
    with open(whisper_result_path, "w", encoding="utf-8") as f:
        f.write(response.text)


def dtw_to_srt_timestamp(dtw: int) -> str:
    """Convert Whisper DTW to HH:mm:ss,ms format."""
    if dtw < 0:
        raise ValueError("DTW cannot be negative.")
    total_ms = dtw * 10
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def whisper_result_to_srt(whisper_result_path: Path, srt_path: Path):
    with open(whisper_result_path, "r", encoding="utf-8") as f:
        whisper_result = json.load(f)

    os.makedirs(srt_path.parent, exist_ok=True)
    with open(srt_path, "w", encoding="utf-8") as f:
        # VAD removes parts of the audio and Whisper.cpp correct the segment start/end but not the word ones
        words_with_dtw = []
        for segment in whisper_result["segments"]:
            if len(segment["text"]) == 0:
                continue
            words = segment.get("words") or []
            if len(words) > 0 and words[0]["t_dtw"] >= 0 and words[-1]["t_dtw"] >= 0:
                dtw_start = words[0]["t_dtw"] + round(100 * (segment["start"] - words[0]["start"]))
                dtw_end = words[-1]["t_dtw"] + round(100 * (segment["end"] - words[-1]["end"]))
            else:
                # No word or no DTW timestamp (t_dtw = -1): use the segment times
                dtw_start, dtw_end = round(100 * segment["start"]), round(100 * segment["end"])
            words_with_dtw.append((segment["text"], dtw_start, dtw_end))

        for i, word_with_dtw in enumerate(words_with_dtw, 1):
            word, word_dtw_start, word_dtw_end = word_with_dtw
            f.write(
                f"{i}\n{dtw_to_srt_timestamp(word_dtw_start)} --> {dtw_to_srt_timestamp(word_dtw_end)}\n{word}\n\n"
            )
