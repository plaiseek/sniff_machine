from pathlib import Path

import json
import os
import requests
import sys


class IncorrectWhisperServer(Exception):
    def __init__(self, address: str, message: str = ""):
        super().__init__(message)
        self.address = address
        self.message = message

    def __str__(self):
        return f"'{self.address}' is not a Whisper.cpp server" + (
            f":\n{self.message}" if len(self.message) > 0 else "."
        )


def try_whisper_server(address: str) -> bool:
    url = f"http://{address}"
    print(f"Checking {url}...")
    try:
        r = requests.get(url, timeout=5)
        r.raise_for_status()
        if "<h1>Whisper.cpp Server</h1>" not in r.text:
            raise IncorrectWhisperServer(address)
    except requests.exceptions.RequestException as e:
        raise IncorrectWhisperServer(address, str(e))


def assert_whisper_servers(addresses: list):
    for address in addresses:
        try:
            try_whisper_server(address)
        except IncorrectWhisperServer as e:
            print(e)
            sys.exit()


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


def mp3_to_whisper_result(
    whisper_address: str,
    mp3_path: Path,
    whisper_result_path: Path,
    language: str = "fr",
) -> None:
    files = {"file": (mp3_path.name, open(mp3_path, "rb"), "audio/mpeg")}
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

    whisper_url = f"http://{whisper_address}/inference"
    response = requests.post(whisper_url, files=files, data=data)
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
    with open(whisper_result_path, "w", encoding="utf-8") as f:
        whisper_result = json.load(f)

    os.makedirs(srt_path.parent, exist_ok=True)
    with open(srt_path, "w", encoding="utf-8") as f:
        # VAD removes parts of the audio and Whisper.cpp correct the segment start/end but not the word ones
        words_with_dtw = [
            (
                segment["text"],
                segment["words"][0]["t_dtw"]
                + int(100 * (segment["start"] - segment["words"][0]["start"])),
                segment["words"][-1]["t_dtw"]
                + int(100 * (segment["end"] - segment["words"][-1]["end"])),
            )
            for segment in whisper_result["segments"]
            if len(segment["text"]) > 0
        ]

        for word_with_dtw in words_with_dtw:
            word, word_dtw_start, word_dtw_end = word_with_dtw
            f.write(
                f"{i}\n{dtw_to_srt_timestamp(word_dtw_start)} --> {dtw_to_srt_timestamp(word_dtw_end)}\n{word}\n\n"
            )


        i = 1
        segment = ""
        segment_dtw_start = None
        for word_with_dtw in words_with_dtw:
            word, word_dtw_start, word_dtw_end = word_with_dtw
            segment += word
            if segment_dtw_start is None:
                segment_dtw_start = word_dtw_start
            if (
                word.endswith(".")
                or word.endswith(",")
                or word.endswith("!")
                or word.endswith("?")
            ):
                f.write(
                    f"{i}\n{dtw_to_srt_timestamp(segment_dtw_start)} --> {dtw_to_srt_timestamp(word_dtw_end)}\n{segment}\n\n"
                )
                i += 1
                segment = ""
                segment_dtw_start = None
