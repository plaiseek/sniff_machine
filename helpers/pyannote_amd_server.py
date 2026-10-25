import helpers.docker as dk
import base64
from pathlib import Path

# The server relies on the DiarizeOutput of pyannote.audio 4.x (exclusive_speaker_diarization)
pyannote_version = "4.0.7"


def build_pyannote_image(
    huggingface_token_path: Path, model: str = "pyannote/speaker-diarization-community-1"
) -> None:
    with open(huggingface_token_path, "r") as f:
        huggingface_token = f.read().strip()

    server_script = f"""import torch
from pyannote.audio import Pipeline
from pyannote.audio.pipelines.utils.hook import ProgressHook
# import torchaudio
from flask import Flask, request, jsonify
import soundfile as sf
import time
from collections import defaultdict
from huggingface_hub import try_to_load_from_cache
from importlib.metadata import version
import json
import math
import os
import threading

app = Flask(__name__)

# Loaded from the copy in the image (HF_HUB_OFFLINE): no token needed
pipeline = Pipeline.from_pretrained("{model}")
pipeline.to(torch.device("cuda"))
# The other gunicorn threads answer /health and read uploads while one diarizes: one diarization at a time
pipeline_lock = threading.Lock()

# Provenance, returned with every answer: the Hugging Face commit of the model copied in the image
config_path = try_to_load_from_cache("{model}", "config.yaml")
info = {{
    "model": "{model}",
    "revision": os.path.basename(os.path.dirname(config_path)) if isinstance(config_path, str) else None,
    "pyannote_audio": version("pyannote.audio"),
}}

accepted_params = {{
    "num_speakers": int,
    "min_speakers": int,
    "max_speakers": int,
}}
def verify_params(params: dict) -> list:
    errors = []
    for key, value in params.items():
        if key not in accepted_params:
            errors.append(f"Unknown key '{{key}}'.")
            continue
        expected_type = accepted_params[key]
        if type(value) is not expected_type:
            errors.append(
                f"Key '{{key}}' expects type '{{expected_type.__name__}}' "
                f"but received '{{type(value).__name__}}'."
            )
        # pyannote takes 0 for "unset", and fails deep in sklearn for negative or huge counts
        elif not 1 <= value <= 50:
            errors.append(f"Key '{{key}}' must be between 1 and 50, got {{value}}.")
    if errors:
        return errors
    # pyannote silently lets num_speakers override min_speakers and max_speakers
    if "num_speakers" in params and ("min_speakers" in params or "max_speakers" in params):
        errors.append("Give either num_speakers, or min_speakers and/or max_speakers.")
    if params.get("min_speakers", 1) > params.get("max_speakers", 50):
        errors.append("min_speakers must not be greater than max_speakers.")
    return errors

class LineProgressHook:
    def __init__(self, every=1.0):
        self.every, self.last = every, {{}}

    def __call__(self, name, _, file=None, total=None, completed=None):
        if completed is None:
            print(f"{{name}}: done", flush=True)
        elif completed >= total or time.monotonic() - self.last.get(name, 0) >= self.every:
            self.last[name] = time.monotonic()
            print(f"{{name}}: {{completed}}/{{total}}", flush=True)

@app.route("/health", methods=["GET"])
def health():
    return {{"status": "ok", "busy": pipeline_lock.locked(), **info}}

@app.route("/diarize", methods=["POST"])
def diarize():
    if "file" not in request.files:
        return jsonify({{"error": "No 'file' given."}}), 400
    audio = request.files["file"]
    if audio.filename == "":
        return jsonify({{"error": "Empty filename."}}), 400

    params = json.loads(request.form.get("params", "{{}}"))
    if errors := verify_params(params):
        print(errors, flush=True)
        return jsonify({{"error": ", ".join(errors)}}), 400

    # One diarization at a time. The upload is decoded under the lock too, so that requests waiting
    # for their turn hold their (spooled) upload, not a decoded waveform, in memory
    with pipeline_lock:
        # Decoded with soundfile and given to pyannote in memory: torchcodec is not needed
        # (Werkzeug spools big uploads to a temporary file it deletes itself)
        try:
            samples, sample_rate = sf.read(audio.stream, dtype="float32", always_2d=True)
        except Exception as e:
            return jsonify({{"error": f"Cannot decode audio: {{e}}"}}), 400
        waveform = torch.from_numpy(samples.T)  # (channel, time)
        try:
            output = pipeline(
                {{"waveform": waveform, "sample_rate": sample_rate}}, hook=LineProgressHook(), **params
            )
        except Exception as e:
            print(e)
            return jsonify({{"error": str(e)}}), 400

    diarization = defaultdict(list)
    for turn, speaker in output.speaker_diarization:
        diarization[speaker].append([turn.start, turn.end])
    # At most one speaker at a time: the turns to send to the SpeechBrain server
    exclusive_diarization = defaultdict(list)
    for turn, speaker in output.exclusive_speaker_diarization:
        exclusive_diarization[speaker].append([turn.start, turn.end])
    # pyannote's own voice embedding of each speaker (rows in the order of labels(), not normalised),
    # None when it has none: an all-zero row (a speaker only heard in overlapped speech) or a NaN one
    # (too little speech), which would also make the JSON invalid. Used to relabel speakers across windows.
    speaker_embeddings = {{}}
    if output.speaker_embeddings is not None:
        for speaker, row in zip(output.speaker_diarization.labels(), output.speaker_embeddings):
            row = [float(x) for x in row.astype("float32")]
            speaker_embeddings[speaker] = row if any(row) and all(map(math.isfinite, row)) else None
    return jsonify({{
        "diarization": diarization,
        "exclusive_diarization": exclusive_diarization,
        "speaker_embeddings": speaker_embeddings,
        "duration": samples.shape[0] / sample_rate,
        **info,
    }})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000)
"""
    base64_server_script = base64.b64encode(server_script.encode()).decode()
    dockerfile = f"""
FROM rocm/pytorch:latest AS base
# pyannote.audio 4 sends usage metrics (audio durations, speaker counts) to otel.pyannote.ai by default
ENV PYANNOTE_METRICS_ENABLED=0
# Running pip as root is expected in this single-purpose image
ENV PIP_ROOT_USER_ACTION=ignore
# torchcodec (pulled by pyannote.audio, and tied to one torch version) is removed: the server decodes the audio
# itself, so pyannote's "torchcodec is not installed correctly" warning (its only one in that module) is silenced
ENV PYTHONWARNINGS=ignore::UserWarning:pyannote.audio.core.io
ENV HF_HOME=/opt/huggingface
# Constrain pip to the ROCm torch/torchaudio of the base image: it fails loudly instead of replacing them with CUDA wheels.
# pip itself is not upgraded: in the Ubuntu 24.04 based images it belongs to Debian and cannot be uninstalled.
RUN python3 -m pip list --format=freeze | grep -iE '^(torch|torchaudio)==' > /tmp/rocm_constraints.txt && \\
    cat /tmp/rocm_constraints.txt && \\
    python3 -m pip install --no-cache-dir -c /tmp/rocm_constraints.txt \\
    flask gunicorn soundfile pyannote.audio=={pyannote_version} && \\
    python3 -m pip uninstall -y torchcodec

# Throwaway stage, the only one that sees the Hugging Face token: it downloads the (gated) model.
# The build log only shows the commands below, never the token itself.
FROM base AS download
COPY hf.token /tmp/hf.token
RUN HF_TOKEN_PATH=/tmp/hf.token python3 -c "from pyannote.audio import Pipeline; Pipeline.from_pretrained('{model}')" && \\
    ! grep -rqsF "$(cat /tmp/hf.token)" "$HF_HOME"

# Final image: the downloaded model without the token (checked by the grep above), loaded offline at runtime
FROM base
COPY --from=download /opt/huggingface/hub /opt/huggingface/hub
ENV HF_HUB_OFFLINE=1
RUN mkdir -p /app && echo {base64_server_script} | base64 -d > /app/pyannote_server.py
ENV PYTHONUNBUFFERED=1
# ENTRYPOINT ["python3", "/app/pyannote_server.py"]
WORKDIR /app
# --timeout 0: the worker would otherwise be killed while diarizing a long audio file.
# --threads 8: /health keeps answering and uploads keep being read during a diarization, even with
# requests waiting for their turn (e.g. from a job killed meanwhile); the pipeline is behind a lock
ENTRYPOINT ["gunicorn", "-w", "1", "--threads", "8", "--timeout", "0", "-b", "0.0.0.0:3000", "pyannote_server:app"]
"""
    dk.build_image("amd_pyannote:latest", dockerfile, files={"hf.token": huggingface_token})


def run_pyannote_container(port: int, rocm_device: int = 0):
    return dk.run_container(
        "amd_pyannote:latest",
        {
            "detach": True,
            "devices": ["/dev/kfd", "/dev/dri"],
            "environment": {"ROCR_VISIBLE_DEVICES": rocm_device},
            "ports": {3000: port},
        },
    )


def stop_all_pyannote_containers():
    dk.stop_containers_by_tag("amd_pyannote:latest")
