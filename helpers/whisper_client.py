from pathlib import Path
import os
import requests


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
        "temperature_inc": "0.2",
        "logprob_thold": "-0.8",
        "no_speech_thold": "0.4",
        "no_context": "true",
        "suppress_nst": "true",
        "vad": "true",
        "vad_threshold": "0.8",
        "vad_min_silence_duration_ms" : "2000",
        "vad_speech_pad_ms": "400",
    }
    response = requests.post(url, files=files, data=data)
    response.raise_for_status()

    os.makedirs(output_folder, exist_ok=True)
    srt_path = Path(f"{output_folder}/{mp3_path.stem}.srt")
    with open(srt_path, "w", encoding="utf-8") as f:
        f.write(response.text)
