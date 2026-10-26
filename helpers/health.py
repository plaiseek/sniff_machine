"""Which of the listed model servers (Whisper, Pyannote, SpeechBrain, wtpsplit) a step can use.

The servers listed in servers.py may run on machines that are not always on: a server that does
not answer is left out with a warning, and a step stops only when none of its servers answers.
"""

import sys
import time

import requests


def ready_servers(kind: str, addresses: list, list_name: str, model_of, busy_timeout_s: float = 600) -> tuple[str | None, list[str]]:
    """(model, addresses) of the listed servers that answer /health. A server whose only thread is
    working (no answer within 5 s) or that is loading a model (HTTP 503) is kept: work sent to it
    waits. model_of(answer of /health) gives the model a server runs, None when it does not say
    (Whisper). The model is part of what makes a result "done", so it is never guessed: when every
    server kept is busy, they are waited for (up to busy_timeout_s) until one says it. Exits if no
    server is left, or if they run different models."""
    if not addresses:
        sys.exit(f"No {kind} server is listed in servers.py ({list_name})")
    models, ready, busy = set(), [], []
    for address in addresses:
        print(f"Checking http://{address}/health...", flush=True)
        try:
            r = requests.get(f"http://{address}/health", timeout=5)
            if r.status_code == 503:
                ready.append(address)  # loading a model: it answers once loaded
                busy.append(address)
                continue
            r.raise_for_status()
            models.add(model_of(_object(r)))
            ready.append(address)
        except requests.exceptions.ReadTimeout:
            ready.append(address)  # its only thread is working (or the model is still loading)
            busy.append(address)
        except (requests.exceptions.RequestException, ValueError, KeyError, TypeError) as e:
            print(f"Warning: {kind} server {address} is not ready, it is left out: {_reason(e)}", flush=True)
    if not ready:
        sys.exit(f"No {kind} server of {addresses} is ready (see model_servers/check_servers.py)")
    deadline = time.monotonic() + busy_timeout_s
    while not models and busy:
        if time.monotonic() > deadline:
            sys.exit(f"No {kind} server answered /health within {busy_timeout_s:.0f} s (busy: {busy})")
        print(f"{kind} servers {busy} are busy or loading: waiting for one to answer...", flush=True)
        time.sleep(10)
        for address in busy:
            try:
                r = requests.get(f"http://{address}/health", timeout=5)
                if r.status_code == 503:
                    continue
                r.raise_for_status()
                models.add(model_of(_object(r)))
                break
            except requests.exceptions.ReadTimeout:
                pass
            except (requests.exceptions.RequestException, ValueError, KeyError, TypeError) as e:
                print(f"Warning: {kind} server {address} is not ready, it is left out: {_reason(e)}", flush=True)
                ready.remove(address)
                busy.remove(address)
                break
        if not ready:
            sys.exit(f"No {kind} server of {addresses} is ready (see model_servers/check_servers.py)")
    if len(models) > 1:
        sys.exit(f"The {kind} servers run different models: {sorted(models, key=str)}")
    if len(ready) < len(addresses):
        print(f"{kind}: {len(ready)} of {len(addresses)} servers used: {', '.join(ready)}", flush=True)
    return (models.pop() if models else None), ready


def named_model(answer: dict) -> str:
    """The model a server says it runs in its /health answer ({"model": ...}): a model_of for ready_servers()."""
    if not isinstance(answer.get("model"), str) or not answer["model"]:
        raise ValueError(f"No model in the /health answer: {answer}")
    return answer["model"]


def _object(r: requests.Response) -> dict:
    """The JSON object of an answer (ValueError otherwise: the server is left out)."""
    answer = r.json()
    if not isinstance(answer, dict):
        raise ValueError(f"Unexpected /health answer: {answer!r}")
    return answer


def _reason(error: Exception) -> str:
    """Why a server is not ready, in a few words (a refused connection's message is several lines long)."""
    if isinstance(error, requests.exceptions.HTTPError):
        return f"HTTP {error.response.status_code}"
    if isinstance(error, requests.exceptions.RequestException):
        return type(error).__name__
    return str(error)
