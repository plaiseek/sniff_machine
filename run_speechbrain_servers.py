from helpers.speechbrain_amd_server import *

stop_all_speechbrain_containers()

gpu_instances = {0: [8040]}
build_speechbrain_image(model="speechbrain/spkrec-ecapa-voxceleb")

container_prefix_pairs = []
for device, ports in gpu_instances.items():
    for port in ports:
        container = run_speechbrain_container(port, device)
        container_prefix_pairs.append((container, f"[{port},{device}]"))

try:
    print("SpeechBrain containers started. Press Ctrl+C to stop.")
    dk.print_containers_logs(container_prefix_pairs)
except KeyboardInterrupt:
    print("Stopping SpeechBrain containers...")
    dk.stop_containers([c for c, _ in container_prefix_pairs])
