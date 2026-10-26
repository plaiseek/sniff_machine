---
name: work-status
description: Says what this machine is doing for Sniff Machine and how the last runs ended - the model servers (up or down, launchers, containers, GPU memory), the jobs and scripts running now, how each job and LLM question last finished, and the disk space left - without starting, stopping or deleting anything. Use for requests like "what is running?", "is the job finished?", "did last night's job work?", "are the servers up?", "how much disk space is left?", "can I start another job?" (in French qu'est-ce qui tourne, le job est-il fini, état des serveurs, espace disque).
---

# Work status

Read-only: this skill never starts, stops, kills, deletes or edits anything (a job's `--status` only takes its lock for an instant, creating the empty `cache/locks/<job>.py.lock` if it is missing). Run every command from the repository root. To act on what it finds, use the matching skill: `model-servers` (servers), `pipeline-job` (jobs), `ask-transcripts` (LLM questions), `query-database` (what the database holds).

## 1. Model servers

```bash
python3.11 model_servers/check_servers.py
ps -ef | grep "[r]un_.*_servers"
docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' | grep amd_
```

One line per server of `servers.py`: `OK`, `BUSY` (working), `LOADING` or `DOWN`, and `N of M up (the others are left out)` for a kind with some servers down. Jobs leave out the servers that are DOWN (`Warning: ... is not ready, it is left out`), so a DOWN line is a problem only when every server of a kind is DOWN: the script then ends with `No server up for: <kinds>` (exit code 1). A job also stops (exit code 4) when all the servers of a step stay busy for more than 10 minutes, or run different models. A Whisper server says `OK` even while it transcribes. `ps` shows the launchers running on this machine (they print the containers' logs); `docker ps` shows their containers (random names; images `amd_whisper`, `amd_pyannote`, `amd_speechbrain`, `amd_wtpsplit`; the published port is the one of the address in `servers.py`) and since when they are up. vLLM runs on another machine (`VLLM_SERVERS`): only `check_servers.py` sees it.

GPU memory (the GPU of the servers is the card with several GB; ROCm device 0 here is `card1`):

```bash
for c in /sys/class/drm/card[0-9]/device; do echo "$c: $(( $(cat $c/mem_info_vram_used) / 1048576 )) of $(( $(cat $c/mem_info_vram_total) / 1048576 )) MiB used"; done
```

The models stay loaded, so most of it is used even when nothing runs. `GPU out of memory` errors in a log mean the card is too full for the servers sharing it: tell the user (fewer servers on that GPU is their decision).

## 2. What is running now

```bash
for f in $(grep -l "run_job(" jobs/2*.py); do python3.11 "$f" --status; done
ps -eo pid,etime,args | grep -E "python3(\.11)? (-u )?(pipeline|speakers|explore|jobs|admin|\.claude/skills/[a-z-]+)/" | grep -v grep || echo "no script running"
```

Only the job files (those that call `run_job(`) take `--status`: it prints `<file>.py: RUNNING` or `NOT RUNNING`, and never starts the job. Never run another script of `jobs/` with `--status`: a one-off script (`write-pipeline-script` skill) ignores it and runs. `ps` shows every job, pipeline script, speaker script and LLM question running, how long it has run (`etime`) and its full command line (the selection and options). A command started from Claude Code with `run_in_background` may show twice (its shell, and the script).

## 3. How the last runs ended

```bash
for f in jobs/*.log; do last=$(tail -n 1 "$f"); case "$last" in 'Job finished with exit code'*|'Finished with exit code'*) echo "$f: $last";; *) echo "$f: NO FINAL LINE (last: $last)";; esac; done
tail -n 20 jobs/<file>.log
```

A job's log ends each run with `Job finished with exit code N: <meaning>`; an LLM question run in the background (logs named `jobs/<date>_ask_<name>.log`) ends with `Finished with exit code N.` after its findings (logs from before 2026-10-26 17:00 may end with the `Asked on N of M contents ... not verdicts.` line instead: finished too). A log with `NO FINAL LINE`: if step 2 shows its run, it is still going; otherwise read its last lines. A job log ending with a Python error has a broken job file (`pipeline-job` skill, step 4); any other job log, and an ask log with a `Question N, with MODEL on` line, belongs to a run killed after it started (out of memory, reboot): it can be run again, it resumes. An ask log without that line stopped before starting, and its last line says why. The meaning of each exit code, and what to do, is in the `pipeline-job` skill (jobs) and the `ask-transcripts` skill (questions). Logs are appended to: a log holds every run of its job, the last one at the end. `cache/*_servers.log` are the launchers' logs (they may lag: `docker logs --tail 30 <container>` shows a server's own log), `cache/embed_speaker_turns.log` that of `pipeline/embed_speaker_turns.py` as the `speaker-identity` skill runs it; `cache/store_words.log` and `cache/split_sentences.log` come from earlier runs by hand.

## 4. Disk space

```bash
df -h .
du -sh cache/*/ cache/test.db exports/ 2>/dev/null | sort -h
docker system df
```

Every downloaded or computed file is under `cache/`, and what was exported to hand over under `exports/`; the Docker images of the servers take about 25 to 33 GB each, and rebuilding one leaves the old one behind (`RECLAIMABLE` in `docker system df`). When less than about 50 GB are left, tell the user: each hour of video or audio takes about 200 MB in `cache/` (its mp3 and its 16 kHz wav), so a channel of 500 hours needs about 100 GB. Never delete anything to make room: `cache/` cannot be recomputed, and removing Docker images or containers is the user's decision.

## 5. Report

In a few lines: the servers (which kinds have at least one server up, which addresses are down), what runs now (jobs, scripts, questions), how the last run of each recent job or question ended (with its exit code's meaning), the disk space left and the GPU memory used. When the user asks whether to start more work, say what it would share: another job on the same Whisper or Pyannote servers waits behind the running one; one that embeds or splits stops with exit code 4 if the only SpeechBrain or wtpsplit server stays busy for more than 10 minutes when it starts (start it once `check_servers.py` says OK for them); and two jobs must never work on the same contents at the same time.
