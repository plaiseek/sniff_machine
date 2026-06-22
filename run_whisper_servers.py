from helpers.amd_whisper_server import *

# gpu_instances = {0: [8070, 8071, 8072], 1: [8073, 8074, 8075]}
# build_whisper_docker_image(gfx_version="12.0.1")

gpu_instances = {0: [8070, 8071]}
build_whisper_docker_image(gfx_version="11.0.0")

for device, ports in gpu_instances.items():
    for port in ports:
        run_whisper_docker_image(port, device)

try:
    print("Whisper Docker images started. Press Ctrl+C to stop.")
    print_logs_of_running_containers()
except KeyboardInterrupt:
    print("Stopping Whisper Docker images...")
    stop_whisper_docker_images()
