# Addresses (host:port) of the model servers that the scripts and jobs send work to.
# Edit them to match where you run run_*_servers.py (gpu_instances = {device: [ports]}).
# Each server gets one worker thread: list a server once per container.

WHISPER_SERVERS = [f"192.168.0.41:{port}" for port in [8070, 8071, 8072, 8073, 8074, 8075]]
PYANNOTE_SERVERS = ["127.0.0.1:8050"]
SPEECHBRAIN_SERVERS = ["127.0.0.1:8040"]
WTPSPLIT_SERVERS = ["127.0.0.1:8060"]
