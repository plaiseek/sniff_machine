import os
import signal
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)  # the database and cache/ paths are relative to the repository root
sys.path.insert(0, str(ROOT))

import helpers.docker as dk
from helpers.local_servers import launch_settings
from helpers.pyannote_amd_server import build_pyannote_image, run_pyannote_container, stop_all_pyannote_containers

# Ctrl+C, `kill -INT <pid>` and `kill <pid>` (SIGTERM) stop this launcher, and its containers once started:
# a launcher started in the background may have SIGINT ignored, and a plain kill would leave them running
signal.signal(signal.SIGINT, signal.default_int_handler)
signal.signal(signal.SIGTERM, signal.default_int_handler)

# The token's Hugging Face account must have accepted the conditions of
# https://huggingface.co/pyannote/speaker-diarization-community-1
# This machine's GPUs, ports and build options come from local_servers.yaml (helpers/local_servers.py),
# read before anything is stopped; these are the defaults without the file
gpu_instances, build = launch_settings("pyannote", {"gpus": {0: [8050]}, "huggingface_token_path": "hf.token"}, build_pyannote_image)
stop_all_pyannote_containers()
build_pyannote_image(**build)

container_prefix_pairs = []
try:
    for device, ports in gpu_instances.items():
        for port in ports:
            container = run_pyannote_container(port, device)
            container_prefix_pairs.append((container, f"[{port},{device}]"))
    print("Pyannote containers started. Press Ctrl+C to stop.")
    dk.print_containers_logs(container_prefix_pairs)
except KeyboardInterrupt:
    # A second Ctrl+C or kill must not cut the cleanup short
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    print("Stopping Pyannote containers...")
    # By image, not only those listed: also the one whose start was interrupted (this launcher removed
    # every container of the image when it began, and only one launcher of a kind runs at a time)
    stop_all_pyannote_containers()
