import subprocess
from pathlib import Path


def convert_to_16k_mono_wav(input_path: Path, output_path: Path, verbose: bool = True) -> None:
    """Convert any audio file to 16 kHz mono PCM WAV, streaming ffmpeg logs."""
    cmd = [
        "ffmpeg",
        "-y",
        "-nostdin",
        "-hide_banner",
        "-fflags",
        "+discardcorrupt+genpts",
        "-err_detect",
        "ignore_err",
        "-i",
        str(input_path),
        "-vn",
        "-sn",
        "-dn",
        "-map",
        "0:a:0",
        "-af",
        "aresample=async=1:resampler=soxr",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(output_path),
    ]
    proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, text=True, bufsize=1)

    if verbose:
        buffer = ""
        for char in iter(lambda: proc.stderr.read(1), ""):
            if char in ("\n", "\r"):
                if buffer.strip():
                    print(f"[ffmpeg] {buffer.strip()}", flush=True)
                buffer = ""
            else:
                buffer += char
        if buffer.strip():
            print(f"[ffmpeg] {buffer.strip()}", flush=True)

    if proc.wait() != 0:
        raise RuntimeError(f"ffmpeg failed with exit code {proc.returncode}")
