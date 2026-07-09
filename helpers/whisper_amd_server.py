import helpers.docker as dk


def build_whisper_image(
    gfx_version="11.0.0", ggml_model="large-v3", vad_model="silero-v6.2.0"
) -> None:
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
    "--model", "models/ggml-{ggml_model}.bin", \\
    "--no-flash-attn", "--dtw", "{ggml_model.replace('-', '.')}", \\
    "--vad-model", "models/ggml-{vad_model}.bin", "--print-progress"]
"""
    dk.build_image("amd_whisper:latest", dockerfile)


def run_whisper_container(port: int, rocm_device: int):
    return dk.run_container(
        "amd_whisper:latest",
        {
            "detach": True,
            "devices": ["/dev/kfd", "/dev/dri"],
            "environment": {"ROCR_VISIBLE_DEVICES": rocm_device},
            "ports": {3000: port},
        },
    )


def stop_all_whisper_containers():
    dk.stop_containers_by_tag("amd_whisper:latest")
