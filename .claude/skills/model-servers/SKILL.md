---
name: model-servers
description: Checks, starts or stops the GPU model servers of this repository (Whisper transcription, Pyannote diarization, SpeechBrain voice embeddings, wtpsplit sentence segmentation), runs a kind of them on this machine, and changes the server addresses in servers.py. Use when model_servers/check_servers.py reports a server DOWN, when the user asks to start or stop servers, or when a job stops because no server of a step can be used (exit code 4).
---

# Model servers

The servers are Docker containers built and run by the `model_servers/run_*_servers.py` scripts, on machines with an AMD GPU (ROCm). Jobs and scripts find them through the addresses in `servers.py` (shared by every machine). Which servers a machine runs, on which GPUs and ports, is in its own `local_servers.yaml` (git-ignored; template: `local_servers.example.yaml`): a launcher whose kind has no section there refuses to start (`local_servers.yaml runs no <kind> server on this machine`); without the file, it uses its defaults. They leave out a listed server that cannot be reached, answers an HTTP error other than 503, or answers something unexpected (`Warning: <Kind> server <address> is not ready, it is left out: ...`); a busy server (no answer within 5 s) or a loading one (503) is kept. They stop (a job with exit code 4, a `pipeline/` script with exit code 1) when no server of a step answers (`No <Kind> server of [...] is ready`), when every server kept stays busy for 600 s (`No <Kind> server answered /health within 600 s (busy: [...])`: they work for another job, so run it again later, do not restart them), or when they run different models (`The <Kind> servers run different models: [...]`: tell the user). Which servers answer is decided when a step starts: a server started later is used by the next run only.

| Kind | Used by the step | Start script | Default port(s) | Address list in `servers.py` |
|---|---|---|---|---|
| whisper | transcribe | `python3.11 model_servers/run_whisper_servers.py` | 8070, 8071 | `WHISPER_SERVERS` |
| pyannote | diarize | `python3.11 model_servers/run_pyannote_servers.py` | 8050 | `PYANNOTE_SERVERS` |
| speechbrain | embed (voice embeddings) | `python3.11 model_servers/run_speechbrain_servers.py` | 8040 | `SPEECHBRAIN_SERVERS` |
| wtpsplit | split (sentences) | `python3.11 model_servers/run_wtpsplit_servers.py` | 8060 | `WTPSPLIT_SERVERS` |
| vllm | ask (`explore/ask_transcripts.py`) | none: the user runs vLLM (`vllm serve <model> --reasoning-parser <its parser>`) | 8000 | `VLLM_SERVERS` |

## Check

```bash
python3.11 model_servers/check_servers.py                 # every server in servers.py
python3.11 model_servers/check_servers.py transcribe      # only those a step needs (transcribe, diarize, embed, split, ask) or of a kind (whisper, ...)
```

`OK`: ready. `BUSY`: working on a request (or still loading); work sent to it waits. Whisper never says `BUSY`: it says `OK` even while it transcribes. `LOADING`: Whisper is loading another model; work sent to it waits. `DOWN`: not reachable, or still starting (a starting Whisper server opens its port only once its model is loaded). A kind with some servers down gets a line `N of M up (the others are left out)`. Exit code 1 when a kind asked for has no server up (`No server up for: <kinds>`), or for an unknown kind (`Unknown server kind(s) ...`); 0 otherwise. It only checks `servers.py`; for another address: `curl -s -m 5 -o /dev/null -w '%{http_code}\n' http://<host>:<port>/health` (vLLM: `/v1/models`): `200` OK, `503` LOADING, `000` with curl exit code 28 BUSY (timed out), `000` with exit code 7 DOWN.

## When a server is DOWN

1. Tell the user which address is down, and whether a step they need has no server left (only then is that step blocked). A `vllm` server is never started from this repository. If the host is not `127.0.0.1` (e.g. `192.168.0.41`), the server runs on another machine: you cannot start it from here.
2. If it is `127.0.0.1` and the user asked you to start it: check that Docker answers (`docker ps`); if not, stop and tell the user to start Docker. The other `127.0.0.1` servers of that kind must be DOWN too (see Rules). For pyannote, first check `test -f hf.token` (a Hugging Face token that accepted the conditions of `pyannote/speaker-diarization-community-1`; the script reads it only after removing the pyannote containers): if it is missing, stop and tell the user.
3. If the script of that kind is already running (`ps -ef | grep "[r]un_pyannote_servers"` prints a line, with your kind's script name), look at its containers: `docker ps -a --filter ancestor=amd_pyannote:latest --format '{{.Names}} {{.Status}} {{.Ports}}'` (`-a`: a container that crashed stays, `Exited`). A container `Exited` crashed: show the user its `docker logs --tail 30 <name>`, and stop (restarting is their decision while another server of the kind is up). Otherwise the servers are starting: go to step 4. If no script runs, check the disk space (`df -h /`: an image takes 25 to 33 GB) and start them in the background, with a log:
   ```bash
   nohup python3.11 -u model_servers/run_pyannote_servers.py > cache/pyannote_servers.log 2>&1 &
   ```
   Every start builds the Docker image: a few minutes with Docker's cached layers, 10 minutes to more than an hour for a first build (if the log still prints build steps after 90 minutes, it is still building: tell the user).
4. Wait for `OK` with this loop run in the background (your kind, script and `127.0.0.1` port; for several ports, e.g. Whisper's 8070 and 8071, wait for the last one and check them all at the end; the exit code of `check_servers.py` is not used here). It stops at `OK`, when the script is gone, at a `Traceback` or `Failed to build` in the log (a log written by this start: `>` above), or after 90 minutes:
   ```bash
   for i in $(seq 90); do python3.11 model_servers/check_servers.py pyannote | grep -q '127.0.0.1:8050 *OK' && break; ps -ef | grep -q '[r]un_pyannote_servers' || break; grep -q -e Traceback -e 'Failed to build' cache/pyannote_servers.log && break; sleep 60; done; python3.11 model_servers/check_servers.py pyannote
   ```
   If it is not `OK`, stop and show the user the last 30 lines of the log and of each container of that kind (`docker ps -a --filter ancestor=amd_pyannote:latest --format '{{.Names}} {{.Status}} {{.Ports}}'`, then `docker logs --tail 30 <name>`).

## Run a kind of servers on this machine

Only when the user asks:

1. Check Docker and the containers of that kind already here: `docker ps -a --filter ancestor=amd_<kind>:latest --format '{{.Names}} {{.Status}} {{.Ports}}'` (changing running ones means a restart: see Rules).
2. Find the GPU. The keys of `gpus` in `local_servers.yaml` are ROCm device indexes, not the N of `/sys/class/drm/cardN`. `for n in /sys/class/kfd/kfd/topology/nodes/*; do echo $n $(grep -h -e gfx_target_version -e simd_count $n/properties); done` prints one line per node: the one with `gfx_target_version 0` is the CPU, and ROCm device N is the (N+1)-th node with a gfx target; the largest `simd_count` is the discrete GPU. Here node 1 (`110000` = gfx 11.0.0, 160 SIMDs) is device 0, the Radeon RX 7900 GRE (16 GiB, `/sys/class/drm/card1`, which also drives the monitors), and node 2 (`100306`) the integrated GPU (`card0`, 512 MiB). Whisper's `gfx_version` (in `local_servers.yaml`) must match the device's target.
3. Check the free VRAM (bytes): `for c in /sys/class/drm/card?/device; do echo $c $(cat $c/mem_info_vram_used $c/mem_info_vram_total); done`. One Whisper large-v3 server needs about 4.3 GB (3.1 GB of model plus buffers); measure the other kinds before and after a start. Here 2 Whisper + Pyannote + SpeechBrain + wtpsplit use about 13 to 14.2 GiB of 16, the desktop included: another Whisper does not fit.
4. With the user, set the kind's section of `local_servers.yaml` (copy `local_servers.example.yaml` if the file is missing, and keep only the kinds this machine runs), and add the `127.0.0.1:<port>` addresses to `servers.py` next to the others of that kind (keep the remote ones: those down are left out). Every server of a kind must run the same model (e.g. wtpsplit's `sat_model`), or the jobs stop with `The <Kind> servers run different models`.
5. Start and wait for `OK`: steps 2 to 4 above.

## Rules

- **Never run a `model_servers/run_*_servers.py` script while `model_servers/check_servers.py` says OK, BUSY or LOADING for a `127.0.0.1` server of that kind, or while `docker ps --filter ancestor=amd_<kind>:latest` shows a running container of that kind**: it first removes every container of that kind on this machine, listed in `servers.py` or not, which kills the work in progress of running jobs. Run it only when all of them are DOWN.
- A server can be stuck and still say OK or BUSY (e.g. a GPU hang): a job then ends with exit code 5 and a line saying it `is probably stuck` (no answer within a day). Tell the user which server; restarting it is their decision.
- A `500 Server Error` from Whisper's `/inference` while it says OK is no reason to restart it when the container log (`docker logs --tail 50 <name>`) shows `Final speech segments after filtering: 0`: whisper.cpp then failed because its VAD found no speech (mostly music); the job tries another server once, and a re-run may store an empty transcription. Report other 500 errors to the user.
- Stop the servers you started for a batch (a job, a `pipeline/` script, an embedding run) once that batch and its follow-ups are done: idle, they keep their model in GPU memory and dozens of gunicorn threads. Stop other servers only when the user asks. To stop the servers of a kind, stop their launcher: Ctrl+C in its terminal, or `kill <pid>` (SIGTERM) for one in the background. It removes the containers of its kind, prints `Stopping <Kind> containers...` and exits (stopped while it still builds the image, it just exits: no container was started):
  ```bash
  ps -eo pid,args | grep -E "^ *[0-9]+ python3(\.11)? (-u )?model_servers/run_pyannote_servers.py"   # its pid (not a shell that mentions it)
  kill <that pid>
  docker ps -a --filter ancestor=amd_pyannote:latest      # nothing left
  ```
  A launcher started before 2026-10-26 has no SIGTERM handler: a plain `kill` ends it without removing its containers, so send it `kill -INT <pid>` (as Ctrl+C). Containers whose launcher is gone, or still there after that, are removed with the matching function: `python3.11 -c "from helpers.pyannote_amd_server import stop_all_pyannote_containers as stop; stop()"` (other kinds: `stop_all_<kind>_containers` in `helpers.<kind>_amd_server`).
- Edit `servers.py` only to put the addresses the user gave you. Each address is one server and gets one worker (a vLLM address gets several requests at once); list each server once.
- The GPUs, ports and build options of this machine are in `local_servers.yaml` (`gpus: {<device>: [<ports>]}` per kind; text in quotes). Change them only when the user asks, and update `servers.py` to match. Never commit `local_servers.yaml`: each machine has its own.
