import helpers.docker as dk
import base64
from pathlib import Path

# torchcodec_version has to match the Pytorch version used in rocm/pytorch:latest
# https://github.com/meta-pytorch/torchcodec#compatibility-with-torch-versions
torchcodec_version = "v0.10.0"


def build_pyannote_image(
    huggingface_token_path: Path, model: str = "pyannote/speaker-diarization-community-1"
) -> None:
    with open(huggingface_token_path, "r") as f:
        huggingface_token = f.read()

    server_script = f"""import torch
from pyannote.audio import Pipeline
from pyannote.audio.pipelines.utils.hook import ProgressHook
# import torchaudio
from flask import Flask, request, jsonify
from pathlib import Path
from werkzeug.utils import secure_filename
import time
from collections import defaultdict
import json

app = Flask(__name__)
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

pipeline = Pipeline.from_pretrained("{model}",token="{huggingface_token}")
pipeline.to(torch.device("cuda"))

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

@app.route("/ready", methods=["GET"])
def ready():
    return {{}}

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

    save_path = UPLOAD_DIR / secure_filename(audio.filename)
    audio.save(save_path)
    try:
        output = pipeline(save_path, hook=LineProgressHook(), **params)
    except Exception as e:
        print(e)
        return jsonify({{"error": str(e)}}), 400

    diarization = defaultdict(list)
    for turn, speaker in output.speaker_diarization:
        diarization[speaker].append([turn.start, turn.end])        
    return jsonify({{"diarization": diarization}})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000)
"""
    base64_server_script = base64.b64encode(server_script.encode()).decode()
    dockerfile = f"""
FROM rocm/pytorch:latest
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg pkg-config \
    libavcodec-dev libavformat-dev libavutil-dev \
    libswscale-dev libavdevice-dev libavfilter-dev libswresample-dev \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --upgrade pip && pip install flask gunicorn pyannote.audio && \
    pip uninstall -y torchcodec
RUN pip install pybind11 setuptools wheel cmake ninja && \
    git -c advice.detachedHead=false clone --depth 1 --branch {torchcodec_version} https://github.com/pytorch/torchcodec.git /tmp/torchcodec && \
    cd /tmp/torchcodec && \
    export pybind11_DIR=$(python3 -c "import pybind11; print(pybind11.get_cmake_dir())") && \
    export CMAKE_PREFIX_PATH="${{pybind11_DIR}}:${{CMAKE_PREFIX_PATH}}" && \
    export TORCHCODEC_DISABLE_COMPILE_WARNING_AS_ERROR=1 && \
    export I_CONFIRM_THIS_IS_NOT_A_LICENSE_VIOLATION=1 && \
    pip install . --no-build-isolation && \
    rm -rf /tmp/torchcodec
RUN python -c "from pyannote.audio import Pipeline; Pipeline.from_pretrained('{model}',token='{huggingface_token}')"
RUN mkdir -p /app && echo {base64_server_script} | base64 -d > /app/pyannote_server.py
# ENTRYPOINT ["python", "/app/pyannote_server.py"]
WORKDIR /app
ENTRYPOINT ["gunicorn", "-w", "1", "--threads", "1", "--timeout", "120", "-b", "0.0.0.0:3000", "pyannote_server:app"]
"""
    dk.build_image("amd_pyannote:latest", dockerfile)


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
