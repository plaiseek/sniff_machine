"""Which model servers this machine runs, and how: local_servers.yaml at the repository root,
git-ignored, one per machine (copy local_servers.example.yaml). servers.py lists the addresses of
every machine's servers; this file says which of them this machine starts.

model_servers/run_<kind>_servers.py reads the `<kind>:` section: `gpus` ({ROCm device: [ports]}) and
options of build_<kind>_image() (e.g. Whisper's gfx_version), which replace the launcher's defaults.
Without the file, a launcher uses its defaults; with it, a kind that has no section (or no port)
does not run on this machine.
"""

import inspect
import sys
from pathlib import Path

import yaml

PATH = Path(__file__).resolve().parents[1] / "local_servers.yaml"


def launch_settings(kind: str, defaults: dict, build_image, path: Path = PATH) -> tuple[dict[int, list[int]], dict]:
    """(gpu_instances, keyword arguments of build_image) for `kind`: `defaults` ({"gpus": ..., build
    options...}) updated by the file's `kind:` section. Exits, before anything is stopped or built,
    when the file runs no server of this kind or holds a mistake."""
    if not path.is_file():
        print(f"No {path.name}: the launcher's defaults (see {path.stem}.example{path.suffix} to set this machine's)", flush=True)
        settings = dict(defaults)
    else:
        try:
            with open(path, encoding="utf-8") as f:
                document = yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            sys.exit(f"{path.name} is not valid YAML: {e}")
        if not isinstance(document, dict):
            sys.exit(f"{path.name} must map each kind to its settings (whisper:, pyannote:, ...)")
        section = document.get(kind)
        if not isinstance(section, dict) or not section.get("gpus"):
            sys.exit(f"{path.name} runs no {kind} server on this machine (no {kind}: section with gpus): nothing started")
        settings = {**defaults, **section}
        print(f"{kind}: settings of {path.name}", flush=True)

    gpu_instances, seen = {}, set()
    gpus = settings["gpus"]
    if not isinstance(gpus, dict):
        sys.exit(f"{kind}: gpus must map ROCm devices to ports, e.g. gpus: {{0: [8050]}}: got {gpus!r}")
    for device, ports in gpus.items():
        # bool is an int in Python: YAML reads yes/no/on/off as booleans
        if isinstance(device, bool) or not str(device).isdigit():
            sys.exit(f"{kind}: gpus: a device is a ROCm device number (0, 1, ...), got {device!r}")
        if not isinstance(ports, list) or not all(isinstance(p, int) and not isinstance(p, bool) and 1 <= p <= 65535 for p in ports):
            sys.exit(f"{kind}: gpus: the ports of device {device} must be a list of port numbers, got {ports!r}")
        if repeated := seen & set(ports):
            sys.exit(f"{kind}: gpus: ports {sorted(repeated)} are given twice")
        seen |= set(ports)
        gpu_instances[int(device)] = list(ports)

    build = {key: value for key, value in settings.items() if key != "gpus"}
    allowed = inspect.signature(build_image).parameters
    if unknown := sorted(set(build) - set(allowed)):
        sys.exit(f"{kind}: unknown option {', '.join(unknown)}: {build_image.__name__}() takes {', '.join(allowed)}")
    for key, value in build.items():
        # YAML guesses types: an unquoted gfx_version: 11.0 is the number 11.0, not the text "11.0.0"
        default = defaults.get(key, allowed[key].default)
        if isinstance(default, str) and not isinstance(value, str):
            sys.exit(f"{kind}: {key} must be text, in quotes (e.g. {key}: \"{default}\"), got {value!r}")
    return gpu_instances, build
