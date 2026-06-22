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
    whisper_url: str = "http://127.0.0.1:3000/inference",
    output_folder: str = ".",
    language: str = "fr",
) -> Path:
    """
    Convert an MP3 file to SRT subtitle format using a remote Whisper inference server.

    This function sends the MP3 file to the specified Whisper API endpoint with
    optimized default parameters for high-quality transcription in the target language.
    If the output `.srt` file already exists, it skips reprocessing to avoid redundant work.

    Args:
        mp3_path (Path): Path to the input MP3 audio file. Must be a valid, readable file.
        whisper_url (str, optional): URL of the Whisper API inference endpoint.
            Defaults to `"http://127.0.0.1:3000/inference"`.
        output_folder (str, optional): Directory where the resulting SRT file will be saved.
            Created if it does not exist. Defaults to current directory (`"."`).
        language (str, optional): ISO 639-1 code for the target transcription language
            (e.g., `"fr"` for French, `"en"` for English). Used as a hint; detection may still occur.
            Defaults to `"fr"`.

    Returns:
        Path: Path to the generated SRT file (always `.srt`, UTF-8 encoded).

    Raises:
        ValueError: If any parameter in `data` is not in the `supported_params` list
            (i.e., an unsupported/unknown API field was included).
        requests.exceptions.RequestException: If the HTTP request fails (e.g., network error, 4xx/5xx response).

    Example:
        >>> srt_file = mp3_to_srt(Path("audio.mp3"), language="en")
        >>> print(f"Transcription saved to {srt_file}")
        Transcription saved to ./audio.en.srt
    """
    srt_path = Path(f"{output_folder}/{mp3_path.stem}.{language}.srt")
    if not srt_path.is_file():
        files = {"file": (mp3_path.name, open(mp3_path, "rb"), "audio/mpeg")}
        data = {
            "language": language,
            "response_format": "srt",
            "beam_size": "5",
            "temperature": "0.0",
            "temperature_inc": "0.4",
            "entropy_thold": "2.20",
            "logprob_thold": "-0.6",
            "no_speech_thold": "0.4",
            "max_context": "0",
            "suppress_nst": "true",
            "split_on_word": "true",
            "vad": "true",
            "vad_threshold": "0.6",
            "vad_min_silence_duration_ms": "500",
            "vad_max_speech_duration_s": "20",
            "vad_speech_pad_ms": "400",
        }

        for key in data.keys():
            if key not in supported_params:
                raise ValueError(f"{key} not supported !")

        response = requests.post(whisper_url, files=files, data=data)
        response.raise_for_status()

        os.makedirs(output_folder, exist_ok=True)
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write(response.text)
    return srt_path
