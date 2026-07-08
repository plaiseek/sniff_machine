from helpers.whisper_amd_server import *

stop_all_whisper_containers()

# gpu_instances = {0: [8070, 8071, 8072], 1: [8073, 8074, 8075]}
# build_whisper_docker_image(gfx_version="12.0.1")

gpu_instances = {0: [8070, 8071]}
build_whisper_image(gfx_version="11.0.0")

container_prefix_pairs = []
for device, ports in gpu_instances.items():
    for port in ports:
        container = run_whisper_container(port, device)
        container_prefix_pairs.append((container, f"[{port},{device}]"))

try:
    print("Whisper containers started. Press Ctrl+C to stop.")
    dk.print_containers_logs(container_prefix_pairs)
except KeyboardInterrupt:
    print("Stopping Whisper containers...")
    dk.stop_containers([c for c, _ in container_prefix_pairs])
