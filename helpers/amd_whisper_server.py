import docker
import io
import os
import sys
import threading


def build_whisper_docker_image(
    gfx_version="11.0.0", ggml_model="large-v3", vad_model="silero-v6.2.0"
) -> None:
    """
    Builds a custom Docker image containing the ROCm-optimized whisper.cpp server.

    The image is built from an AMD ROCm base image and includes:
      - Required system dependencies (git, cmake, g++, ffmpeg)
      - Whisper.cpp source cloned & compiled with HIP support (`GGML_HIP=ON`)
      - Specific GPU architecture target via `AMDGPU_TARGETS`
      - GGML model and VAD model binaries downloaded at build time
    The resulting image runs `whisper-server` on port 3000 by default.

    Args:
        gfx_version (str): ROCm GPU architecture version string (e.g., "11.0.0").
                           Used to compile HIP for the target architecture.
        ggml_model (str): Name of the GGML Whisper model to download (without `.bin`).
                          Models are stored in `models/` inside the container.
        vad_model (str): Name of the VAD (Voice Activity Detection) model to download.

    Raises:
        SystemExit: If Docker image build fails, logs errors and exits with EX_CONFIG.

    Note:
        - Requires Docker daemon running and accessible via `docker.from_env()`.
        - The built image is tagged `"amd_whisper:latest"` for reuse by run functions.
    """
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
    "--model", "models/ggml-{ggml_model}.bin", "--vad-model", "models/ggml-{vad_model}.bin", "--print-progress"]
"""

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
    """
    Starts a new Whisper inference server container bound to a specific ROCm GPU.

    The container runs in detached mode and maps port `3000` (inside) → `port` (host).
    It is configured to use only one GPU (`rocm_device`) via the `ROCR_VISIBLE_DEVICES`
    environment variable, enabling multi-GPU parallel inference on a single host.

    Args:
        port (int): Host-side port to expose the Whisper API (e.g., 3001 → `localhost:3001`).
        rocm_device (int): ROCm device ordinal to bind this container (e.g., `0`, `1`, etc.).

    Returns:
        docker.models.containers.Container: The started container object.

    Raises:
        Exception: If container fails to start (exit code 1), raises an exception with
                   the raw stderr message from Docker.
    
    Note:
        - Requires `/dev/kfd` and `/dev/dri` device access for ROCm GPU support.
        - The container is *not* removed automatically (`remove=False`) so it can be managed later.
        - Container metadata (container, port, rocm_device) is appended to `running_containers`.
    """
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
    """
    Internal helper to tail container logs in real-time and prefix each line.

    This is designed to run in a background thread (daemon=True). Logs are
    decoded as UTF-8 with error replacement for robustness.

    Args:
        container: Docker container object whose logs to stream.
        port (int): Host port the container listens on — used for log prefixing.
        rocm_device (int): ROCm device ID — also included in prefix for clarity.

    Note:
        - Logs are printed directly to stdout using `print()`.
        - If the container is stopped or not found mid-stream, silently exits.
    """
    prefix = f"[{port},{rocm_device}]"
    try:
        for raw_line in container.logs(stream=True, follow=True):
            line = raw_line.decode("utf-8", errors="replace").rstrip("\n")
            if line:
                print(f"{prefix} {line}")
    except docker.errors.NotFound:
        pass


def print_logs_of_running_containers():
    """
    Starts a background thread per container to tail and print logs in real-time.

    All threads are joined (blocked) until all containers stop or process exits.
    Threads are daemon=True so they don’t prevent interpreter shutdown.

    Use after `run_whisper_docker_image()` to monitor inference progress.
    """
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
    """
    Stops *and removes* **all** containers with the `"amd_whisper:latest"` image.

    Useful for global cleanup — e.g., before shutting down the app or resetting state.
    Uses `client.containers.list(all=True, filters={...})` to catch stopped containers too.

    Note:
        - Force removal (`force=True`) ensures even paused/stuck containers are cleaned up.
        - This *does not* rely on `running_containers`, so it’s safer for full cleanup.
    """
    client = docker.from_env()
    for container in client.containers.list(
        all=True, filters={"ancestor": "amd_whisper:latest"}
    ):
        container.remove(force=True)


def stop_whisper_docker_images():
    """
    Stops and removes only the containers *tracked* in `running_containers`.

    This is a lightweight cleanup function for the current session’s active instances.
    It does not affect other containers with the same image.

    Note:
        - Does NOT use filters — only operates on known container references.
        - Safe to call multiple times (idempotent), as removed containers are no longer in `running_containers`.
    """
    for container, _, _ in running_containers:
        container.remove(force=True)

