from pathlib import Path
import os
import requests

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


def mp3_to_srt(
    mp3_path: Path,
    output_folder: str = ".",
    url: str = "http://127.0.0.1:3000/inference",
) -> Path:
    files = {"file": (mp3_path.name, open(mp3_path, "rb"), "audio/mpeg")}
    data = {
        "language": "fr",
        "response_format": "srt",
        "temperature": "0.0",
        "temperature_inc": "0.4",
        "entropy_thold": "2.20",
        "logprob_thold": "-0.6",
        "no_speech_thold": "0.4",
        "max_context": "0",
        "suppress_nst": "true",
        "vad": "true",
        "vad_threshold": "0.65",
        "vad_min_silence_duration_ms": "500",
        "vad_max_speech_duration_s": "30",
        "vad_speech_pad_ms": "400",
        "beam_size": "5",
    }

    for key in data.keys():
        if key not in supported_params:
            raise ValueError(f"{key} not supported !")

    response = requests.post(url, files=files, data=data)
    response.raise_for_status()

    os.makedirs(output_folder, exist_ok=True)
    srt_path = Path(f"{output_folder}/{mp3_path.stem}.srt")
    with open(srt_path, "w", encoding="utf-8") as f:
        f.write(response.text)
