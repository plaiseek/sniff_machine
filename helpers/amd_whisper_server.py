import docker
import io
import os
import sys
import threading

gfx_version = "11.0.0"
ggml_model = "large-v3"
vad_model = "silero-v6.2.0"

dockerfile = f"""
FROM rocm/dev-ubuntu-22.04:6.3-complete

RUN apt-get update && apt-get install -y --no-install-recommends \\
    git cmake make g++ python3 ca-certificates ffmpeg \\
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth=1 https://github.com/ggml-org/whisper.cpp /app
WORKDIR /app
RUN cmake -S . -B build \\
    -DGGML_HIP=ON \\
    -DAMDGPU_TARGETS=gfx{gfx_version.replace('.', '')} \\
    -DCMAKE_BUILD_TYPE=Release \\
    -DBUILD_SHARED_LIBS=OFF \\
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON \\
    && cmake --build build -j$(nproc) --target whisper-server
ENV HSA_OVERRIDE_GFX_VERSION={gfx_version}
RUN bash ./models/download-ggml-model.sh {ggml_model}
RUN bash ./models/download-vad-model.sh {vad_model}
ENTRYPOINT ["/app/build/bin/whisper-server", "--host", "0.0.0.0", "--port", "3000", \\
    "--model", "models/ggml-{ggml_model}.bin", "--vad-model", "models/ggml-{vad_model}.bin"]
"""


def build_whisper_docker_image() -> None:
    print("Building amd_whisper Docker image...")
    client = docker.from_env()
    try:
        logs = client.api.build(
            fileobj=io.BytesIO(dockerfile.encode("utf-8")),
            tag="amd_whisper:latest",
            decode=True,
        )
        for entry in logs:
            if "stream" in entry:
                line = entry["stream"]
                if line[-1] == "\n":
                    line = line[:-1]
                if len(line) == 0:
                    continue
                print(line)
    except docker.errors.BuildError as e:
        if e.build_log:
            for log_entry in e.build_log:
                if "stream" in log_entry:
                    print(log_entry["stream"])
                elif "errorDetail" in log_entry:
                    print(log_entry["errorDetail"]["message"])
        sys.exit(os.EX_CONFIG)


running_containers = []


def run_whisper_docker_image(port: int, rocm_device: int):
    print(f"Running amd_whisper Docker image (port={port},device={rocm_device})...")
    try:
        client = docker.from_env()
        container = client.containers.run(
            "amd_whisper:latest",
            remove=False,
            detach=True,
            devices=["/dev/kfd", "/dev/dri"],
            environment={"ROCR_VISIBLE_DEVICES": rocm_device},
            ports={3000: port},
        )
        running_containers.append((container, port, rocm_device))
        return container
    except docker.errors.ContainerError as e:
        match e.exit_status:
            case 1:
                raise Exception(e.stderr.decode("utf-8"))


def _stream_container_logs(container, port: int, rocm_device: int):
    prefix = f"[{port},{rocm_device}]"
    try:
        for raw_line in container.logs(stream=True, follow=True):
            line = raw_line.decode("utf-8", errors="replace").rstrip("\n")
            if line:
                print(f"{prefix} {line}")
    except docker.errors.NotFound:
        pass


def print_logs_of_running_containers():
    threads = []
    for container, port, rocm_device in running_containers:
        t = threading.Thread(
            target=_stream_container_logs,
            args=(container, port, rocm_device),
            daemon=True,
        )
        t.start()
        threads.append(t)
    for t in threads:
        t.join()


def stop_any_whisper_docker_images():
    client = docker.from_env()
    for container in client.containers.list(
        all=True, filters={"ancestor": "amd_whisper:latest"}
    ):
        container.remove(force=True)


def stop_whisper_docker_images():
    for container, _, _ in running_containers:
        container.remove(force=True)
