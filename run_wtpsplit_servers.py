from helpers.wtpsplit_amd_server import *

stop_all_wtpsplit_containers()

gpu_instances = {0: [8060]}
build_wtpsplit_image(sat_model="sat-12l-sm")

container_prefix_pairs = []
for device, ports in gpu_instances.items():
    for port in ports:
        container = run_wtpsplit_container(port, device)
        container_prefix_pairs.append((container, f"[{port},{device}]"))

try:
    print("Wtpsplit containers started. Press Ctrl+C to stop.")
    dk.print_containers_logs(container_prefix_pairs)
except KeyboardInterrupt:
    print("Stopping Wtpsplit containers...")
    dk.stop_containers([c for c, _ in container_prefix_pairs])
