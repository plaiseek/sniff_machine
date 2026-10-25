---
name: model-servers
description: Checks, starts or stops the GPU model servers of this repository (Whisper transcription, Pyannote diarization, SpeechBrain voice embeddings, wtpsplit sentence segmentation) and changes the server addresses in servers.py. Use when check_servers.py reports a server DOWN, when the user asks to start or stop servers, or when a job fails because a server is unreachable.
---

# Model servers

The servers are Docker containers built and run by the `run_*_servers.py` scripts, on machines with an AMD GPU (ROCm). Jobs and scripts find them through the addresses in `servers.py`.

| Kind | Used by the step | Start script | Default port(s) | Address list in `servers.py` |
|---|---|---|---|---|
| whisper | transcribe | `python run_whisper_servers.py` | 8070, 8071 | `WHISPER_SERVERS` |
| pyannote | diarize | `python run_pyannote_servers.py` | 8050 | `PYANNOTE_SERVERS` |
| speechbrain | embed (voice embeddings) | `python run_speechbrain_servers.py` | 8040 | `SPEECHBRAIN_SERVERS` |
| wtpsplit | split (sentences) | `python run_wtpsplit_servers.py` | 8060 | `WTPSPLIT_SERVERS` |

## Check

```bash
python check_servers.py                 # every server in servers.py
python check_servers.py transcribe      # only those a step needs: transcribe, diarize, embed, split
```

`OK`: ready. `BUSY`: working on a request (or still loading); work sent to it waits, which is fine. `LOADING`: Whisper is loading its model; a job started now waits for it (up to 10 minutes). `DOWN`: not reachable. Exit code 1 when a server is DOWN.

## When a server is DOWN

1. Look at the address. If its host is not `127.0.0.1` (e.g. `192.168.0.41`), the server runs on another machine: **stop and tell the user** which address is down; you cannot start it from here.
2. If it is `127.0.0.1` and the user asked you to start it: check that Docker answers (`docker ps`). If it does not, stop and tell the user to start Docker.
3. Start the servers of that kind in the background, with a log:
   ```bash
   nohup python run_pyannote_servers.py > cache/pyannote_servers.log 2>&1 &
   ```
   The first start builds the Docker image: from 10 minutes to more than an hour. Pyannote also needs the `hf.token` file at the repository root (a Hugging Face token whose account accepted the conditions of `pyannote/speaker-diarization-community-1`); if it is missing, stop and tell the user.
4. Run `python check_servers.py <kind>` every minute until it says `OK` (the model loads after the build). Stop and show the user the last 30 lines of the log if the log shows `Traceback`, `ERROR` or `Failed to build`, if the script is gone (`ps -ef | grep "[r]un_pyannote_servers"` prints nothing, with your kind's script name), or after 90 minutes.

## Rules

- **Never run a `run_*_servers.py` script when `check_servers.py` says OK or BUSY for that kind**: it first stops every running container of that kind, which kills the work in progress of running jobs.
- Stop servers only when the user asks. A `run_*_servers.py` started in a terminal stops its containers on Ctrl+C. One started in the background ignores Ctrl+C signals: stop its containers with the matching function, then kill the script:
  ```bash
  python -c "from helpers.pyannote_amd_server import stop_all_pyannote_containers as stop; stop()"
  kill <pid of run_pyannote_servers.py>
  ```
  (`stop_all_whisper_containers` in `helpers.whisper_amd_server`, `stop_all_speechbrain_containers` in `helpers.speechbrain_amd_server`, `stop_all_wtpsplit_containers` in `helpers.wtpsplit_amd_server`.)
- Edit `servers.py` only to put the addresses the user gave you. Each address is one server and gets one worker; list each server once.
- The ports and GPUs are set by `gpu_instances = {gpu: [ports]}` near the top of each `run_*_servers.py`. Change them only when the user asks, and update `servers.py` to match.
