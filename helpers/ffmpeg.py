import subprocess
from collections import deque
from pathlib import Path


def convert_to_16k_mono_wav(input_path: Path, output_path: Path, verbose: bool = True) -> None:
    """Convert any audio file to 16 kHz mono PCM WAV, streaming ffmpeg logs."""
    # ffmpeg does not create missing output folders
    output_path.parent.mkdir(parents=True, exist_ok=True)
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
    proc = subprocess.Popen(
        cmd, stderr=subprocess.PIPE, text=True, errors="replace", bufsize=1
    )

    # stderr is always read: an unread pipe fills up and blocks ffmpeg on long files,
    # and the last lines explain a failure even when verbose is False
    last_lines = deque(maxlen=10)

    def log(line):
        last_lines.append(line)
        if verbose:
            print(f"[ffmpeg] {line}", flush=True)

    buffer = ""
    try:
        for char in iter(lambda: proc.stderr.read(1), ""):
            if char in ("\n", "\r"):
                if buffer.strip():
                    log(buffer.strip())
                buffer = ""
            else:
                buffer += char
    except BaseException:
        # e.g. KeyboardInterrupt: don't leave ffmpeg running in the background
        proc.kill()
        raise
    if buffer.strip():
        log(buffer.strip())

    if proc.wait() != 0:
        details = "\n".join(f"[ffmpeg] {line}" for line in last_lines)
        raise RuntimeError(f"ffmpeg failed with exit code {proc.returncode}:\n{details}")
