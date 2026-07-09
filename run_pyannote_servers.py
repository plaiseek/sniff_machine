from helpers.pyannote_amd_server import *

stop_all_pyannote_containers()

# Don't forget to go to https://huggingface.co/pyannote/speaker-diarization-community-1 and accept conditions

gpu_instances = {0: [8050]}
build_pyannote_image(huggingface_token_path="hf.token")

container_prefix_pairs = []
for device, ports in gpu_instances.items():
    for port in ports:
        container = run_pyannote_container(port, device)
        container_prefix_pairs.append((container, f"[{port},{device}]"))

try:
    print("Pyannote containers started. Press Ctrl+C to stop.")
    dk.print_containers_logs(container_prefix_pairs)
except KeyboardInterrupt:
    print("Stopping Pyannote containers...")
    dk.stop_containers([c for c, _ in container_prefix_pairs])
