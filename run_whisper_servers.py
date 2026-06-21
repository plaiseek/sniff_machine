from helpers.amd_whisper_server import *
import signal
import sys

# gpu_instances = {0: [8070, 8071, 8072], 1: [8073, 8074, 8075]}
gpu_instances = {0: [8076, 8077]}

build_whisper_docker_image()

for device, ports in gpu_instances.items():
    for port in ports:
        run_whisper_docker_image(port, device)

try:
    print("Whisper Docker images started. Press Ctrl+C to stop.")
    print_logs_of_running_containers()
except KeyboardInterrupt:
    print("Stopping Whisper Docker images...")
    stop_whisper_docker_images()
