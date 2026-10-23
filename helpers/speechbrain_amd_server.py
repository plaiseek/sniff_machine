import helpers.docker as dk
import base64

# SpeechBrain >= 1.1 reads audio with soundfile and works with torchaudio >= 2.9
# (1.0.x calls torchaudio.list_audio_backends, removed in 2.9). 1.1 also renamed use_auth_token -> token.
speechbrain_version = "1.1.2"

# Same base image as the pyannote server: pin it by digest (rocm/pytorch@sha256:...) once it works
rocm_pytorch_image = "rocm/pytorch:latest"

# Plain string, not an f-string: the configuration comes from ENV lines in the Dockerfile
server_script = r"""import json
import math
import os
import time

import numpy as np
import soundfile as sf
import torch
from flask import Flask, request, jsonify
from speechbrain.inference.speaker import EncoderClassifier

MODEL = os.environ["SPEECHBRAIN_MODEL"]
SAMPLE_RATE = 16000

app = Flask(__name__)
# Werkzeug >= 3.1 limits non-file form fields to 500 kB: a long VOD has thousands of turns
app.config["MAX_FORM_MEMORY_SIZE"] = 64 * 1024 * 1024

classifier = EncoderClassifier.from_hparams(
    source=MODEL,
    savedir=os.environ["SPEECHBRAIN_SAVEDIR"],
    run_opts={"device": "cuda:0"},  # ROCm PyTorch exposes the GPU as "cuda"
)
classifier.eval()
with torch.inference_mode():  # also warms up the GPU kernels
    DIMENSION = int(classifier.encode_batch(torch.zeros(1, SAMPLE_RATE)).shape[-1])
print(f"Loaded {MODEL} ({DIMENSION}-dim) on {classifier.device}", flush=True)

accepted_params = {
    "segments": list,
    "min_duration": float,
    "max_duration": float,
    "batch_size": int,
}
def verify_params(params: dict) -> list:
    errors = []
    if "segments" not in params:
        errors.append("Missing key 'segments'.")
    for key, value in params.items():
        if key not in accepted_params:
            errors.append(f"Unknown key '{key}'.")
            continue
        expected_type = accepted_params[key]
        if type(value) is not expected_type:
            errors.append(
                f"Key '{key}' expects type '{expected_type.__name__}' "
                f"but received '{type(value).__name__}'."
            )
    if errors:
        return errors
    for i, segment in enumerate(params["segments"]):
        if (
            type(segment) is not list
            or len(segment) != 2
            or any(type(t) not in (int, float) for t in segment)
            or not 0 <= segment[0] < segment[1]
        ):
            errors.append(f"Segment {i} must be [start, end] in seconds with 0 <= start < end.")
            if len(errors) >= 10:
                break
    # Too short chunks crash the model's convolutions; a long turn is split in chunks of at least max_duration / 2
    min_duration = params.get("min_duration", 1.0)
    max_duration = params.get("max_duration", 30.0)
    if min_duration < 0.1:
        errors.append("'min_duration' must be >= 0.1.")
    if max_duration < 1.0 or max_duration < min_duration:
        errors.append("'max_duration' must be >= 1.0 and >= 'min_duration'.")
    if params.get("batch_size", 16) < 1:
        errors.append("'batch_size' must be >= 1.")
    return errors

def plan_chunks(segments: list, duration: float, min_duration: float, max_duration: float) -> list:
    # (segment index, start, end) per chunk. A segment shorter than min_duration (once clipped to the
    # audio duration) gets no chunk; a segment longer than max_duration is split in equal chunks.
    chunks = []
    for i, (start, end) in enumerate(segments):
        end = min(end, duration)
        if end - start < min_duration:
            continue
        n = math.ceil((end - start) / max_duration)
        step = (end - start) / n
        chunks += [(i, start + k * step, start + (k + 1) * step) for k in range(n)]
    return chunks

def read_chunk(audio_file: sf.SoundFile, start: float, end: float) -> np.ndarray:
    first = int(round(start * SAMPLE_RATE))
    audio_file.seek(first)
    return audio_file.read(int(round(end * SAMPLE_RATE)) - first, dtype="float32")

def embed_chunks(audio_file: sf.SoundFile, chunks: list, batch_size: int) -> dict:
    # Longest first: similar lengths share a batch (little padding) and an OOM shows up on the first batch
    chunks = sorted(chunks, key=lambda chunk: chunk[2] - chunk[1], reverse=True)
    sums = {}
    for b in range(0, len(chunks), batch_size):
        batch = chunks[b : b + batch_size]
        wavs = [read_chunk(audio_file, start, end) for _, start, end in batch]
        max_len = max(len(wav) for wav in wavs)
        padded = torch.zeros(len(wavs), max_len)
        for j, wav in enumerate(wavs):
            padded[j, : len(wav)] = torch.from_numpy(wav)
        wav_lens = torch.tensor([len(wav) / max_len for wav in wavs])  # padding is masked
        with torch.inference_mode():
            embeddings = classifier.encode_batch(padded, wav_lens).squeeze(1)  # (batch, 1, dim) -> (batch, dim)
        embeddings = torch.nn.functional.normalize(embeddings.float(), dim=-1).cpu().numpy()
        for (i, _, _), embedding in zip(batch, embeddings):
            sums[i] = sums.get(i, 0) + embedding
    # Segment embedding = normalised mean of its normalised chunk embeddings
    return {i: s / max(np.linalg.norm(s), 1e-12) for i, s in sums.items()}

@app.route("/health", methods=["GET"])
def health():
    return {"status": "ok", "model": MODEL, "dimension": DIMENSION}

@app.route("/embed", methods=["POST"])
def embed():
    if "file" not in request.files:
        return jsonify({"error": "No 'file' given."}), 400
    audio = request.files["file"]
    if audio.filename == "":
        return jsonify({"error": "Empty filename."}), 400

    try:
        params = json.loads(request.form.get("params", "{}"))
    except json.JSONDecodeError as e:
        return jsonify({"error": f"'params' is not valid JSON: {e}"}), 400
    if type(params) is not dict:
        return jsonify({"error": "'params' must be a JSON object."}), 400
    if errors := verify_params(params):
        print(errors, flush=True)
        return jsonify({"error": ", ".join(errors)}), 400
    segments = params["segments"]

    t0 = time.monotonic()
    try:
        # Read from the upload stream (Werkzeug spools big uploads to a temporary file it deletes itself)
        audio_file = sf.SoundFile(audio.stream)
    except Exception as e:
        return jsonify({"error": f"Cannot decode audio: {e}"}), 400
    with audio_file:
        if audio_file.samplerate != SAMPLE_RATE or audio_file.channels != 1:
            return jsonify({
                "error": f"Expected 16 kHz mono audio, got {audio_file.samplerate} Hz "
                f"with {audio_file.channels} channel(s)."
            }), 400
        chunks = plan_chunks(
            segments,
            audio_file.frames / SAMPLE_RATE,
            params.get("min_duration", 1.0),
            params.get("max_duration", 30.0),
        )
        try:
            embeddings = embed_chunks(audio_file, chunks, params.get("batch_size", 16))
        except torch.cuda.OutOfMemoryError:
            return jsonify({"error": "GPU out of memory: lower 'batch_size' or 'max_duration'."}), 500
        except Exception as e:
            print(e, flush=True)
            return jsonify({"error": str(e)}), 500
        finally:
            torch.cuda.empty_cache()  # give the peak memory back to the other servers on the GPU

    print(
        f"{audio.filename}: {len(segments)} segments, {len(chunks)} chunks, "
        f"{len(segments) - len(embeddings)} skipped, {time.monotonic() - t0:.1f} s",
        flush=True,
    )
    return jsonify({
        "model": MODEL,
        "dimension": DIMENSION,
        # Request order; None = too short or past the end of the audio
        "embeddings": [
            embeddings[i].astype(np.float64).round(6).tolist() if i in embeddings else None
            for i in range(len(segments))
        ],
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000)
"""


def build_speechbrain_image(
    model: str = "speechbrain/spkrec-ecapa-voxceleb", base_image: str = rocm_pytorch_image
) -> None:
    savedir = f"/app/pretrained_models/{model.replace('/', '_')}"
    base64_server_script = base64.b64encode(server_script.encode()).decode()
    dockerfile = f"""
FROM {base_image}
ENV PYTHONUNBUFFERED=1 SPEECHBRAIN_MODEL={model} SPEECHBRAIN_SAVEDIR={savedir}
# Constrain pip to the ROCm torch/torchaudio of the base image: it fails loudly instead of replacing them with CUDA wheels
RUN python3 -m pip list --format=freeze | grep -iE '^(torch|torchaudio)==' > /tmp/rocm_constraints.txt && \\
    cat /tmp/rocm_constraints.txt && \\
    python3 -m pip install --no-cache-dir -c /tmp/rocm_constraints.txt \\
    flask gunicorn soundfile speechbrain=={speechbrain_version}
# Downloads the model (public, no token needed) and checks that speechbrain imports with this torchaudio
RUN python3 -c "import os; from speechbrain.inference.speaker import EncoderClassifier; EncoderClassifier.from_hparams(source=os.environ['SPEECHBRAIN_MODEL'], savedir=os.environ['SPEECHBRAIN_SAVEDIR'])"
# The model is in the image: never check the Hub at startup
ENV HF_HUB_OFFLINE=1
RUN mkdir -p /app && echo {base64_server_script} | base64 -d > /app/speechbrain_server.py
# ENTRYPOINT ["python3", "/app/speechbrain_server.py"]
WORKDIR /app
# --timeout 0: the sync worker would otherwise be killed while embedding the turns of a long audio file
ENTRYPOINT ["gunicorn", "-w", "1", "--threads", "1", "--timeout", "0", "-b", "0.0.0.0:3000", "speechbrain_server:app"]
"""
    dk.build_image("amd_speechbrain:latest", dockerfile)


def run_speechbrain_container(port: int, rocm_device: int = 0):
    return dk.run_container(
        "amd_speechbrain:latest",
        {
            "detach": True,
            "devices": ["/dev/kfd", "/dev/dri"],
            "environment": {"ROCR_VISIBLE_DEVICES": rocm_device},
            "ports": {3000: port},
        },
    )


def stop_all_speechbrain_containers():
    dk.stop_containers_by_tag("amd_speechbrain:latest")
