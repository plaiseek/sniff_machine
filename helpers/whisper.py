from datetime import timedelta
from pathlib import Path
import os
import whisper


def mp3_to_srt(audio_path: Path, output_folder: str = ".", model: str = "large"):
    """
    Transcribe an audio file using Whisper and save the transcription as an SRT subtitle file.
    
    Args:
        audio_path (Path): Path to the audio file to transcribe
        output_folder (str): Directory where the SRT file will be saved (default: current directory)
        model (str): Whisper model size to use for transcription (default: "large")
    
    Returns:
        Path: Path to the generated SRT subtitle file
    
    The function loads the specified Whisper model, transcribes the audio file,
    and converts each segment into SRT format with proper timing and text.
    """
    model = whisper.load_model(model)
    transcribe = model.transcribe(str(audio_path))
    segments = transcribe["segments"]

    os.makedirs(output_folder, exist_ok=True)
    srt_path = Path(f"{output_folder}/{audio_path.stem}.srt")
    with open(srt_path, "w") as file:
        for segment in segments:
            startTime = f"0{timedelta(seconds=int(segment['start']))},000"
            endTime = f"0{timedelta(seconds=int(segment['end']))},000"
            text = segment["text"]
            segmentId = segment["id"] + 1
            file.write(
                f"{segmentId}\n{startTime} --> {endTime}\n{text[1:] if text[0] == ' ' else text}\n\n"
            )
        return srt_path
