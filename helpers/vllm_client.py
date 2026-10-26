"""The vLLM servers (OpenAI-compatible API) that explore/ask_transcripts.py sends its questions to.

Start vLLM with the reasoning parser of the model (see vLLM's list of parsers), e.g. for Qwen3:

    vllm serve <model> --max-model-len 262144 --reasoning-parser qwen3

Without it the JSON answer is constrained from the first token: the model cannot think first.
"""

import heapq
import itertools
import json
import re
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import NamedTuple

import requests

# A window of the first pass, or an excerpt with the thinking of the second, must fit
MIN_MODEL_LEN = 32768


@dataclass
class VllmServers:
    addresses: list[str]  # the listed servers that answered
    model: str
    max_model_len: int | None  # the smallest of the servers (None: not reported)
    reasoning: bool  # the model's reasoning comes apart from its answer (--reasoning-parser): it can think first


@dataclass
class ServerHints:
    """What a vLLM server can do, given by the user (each listed server is assumed alike)."""

    clients: int  # requests in flight at which it is most efficient
    kv_tokens: int  # tokens its KV cache holds: prompts and answers of the requests in flight
    tokens_per_s: float  # tokens it writes per second with `clients` requests in flight


class Reply(NamedTuple):
    answer: dict | None  # None: no answer, see finish_reason
    prompt_tokens: int
    completion_tokens: int
    finish_reason: str | None  # "length": cut by max_tokens; "stop" with no answer: it ended while thinking


class Slots:
    """The requests in flight on one server: at most hints.clients, needing together at most
    hints.kv_tokens tokens (prompt and longest answer), so vLLM never has to pause one to free its
    cache. Waiting requests go by priority (lower first), then in order of arrival."""

    def __init__(self, hints: ServerHints):
        self.hints = hints
        self.cond = threading.Condition()
        self.running = self.tokens = 0
        self.waiting = []  # heap of (priority, arrival)
        self.arrivals = itertools.count()

    @contextmanager
    def slot(self, priority: int, tokens: int):
        tokens = min(tokens, self.hints.kv_tokens)  # more than the cache holds: it runs alone
        with self.cond:
            ticket = (priority, next(self.arrivals))
            heapq.heappush(self.waiting, ticket)
            while self.waiting[0] != ticket or self.running >= self.hints.clients or self.tokens + tokens > self.hints.kv_tokens:
                self.cond.wait()
            heapq.heappop(self.waiting)
            self.running += 1
            self.tokens += tokens
            self.cond.notify_all()  # the next one may fit too
        try:
            yield
        finally:
            with self.cond:
                self.running -= 1
                self.tokens -= tokens
                self.cond.notify_all()


def server_kv_tokens(address: str) -> int | None:
    """The tokens the KV cache of a vLLM server holds, as its /metrics report them (None: unknown)."""
    try:
        r = requests.get(f"http://{address}/metrics", timeout=10)
        r.raise_for_status()
    except requests.exceptions.RequestException:
        return None
    found = re.search(r'^vllm:cache_config_info\{.*\bkv_cache_size_tokens="(\d+)"', r.text, re.MULTILINE)
    return int(found[1]) if found else None


def assert_vllm_servers(addresses: list, model: str | None = None) -> VllmServers:
    """The listed servers that answer and serve `model` (None: the first model of the first server
    answering). A server down, or serving another model, is left out with a warning; exits if none
    is left. Then checks, with one short request, whether the model can think before answering."""
    if not addresses:
        sys.exit("No vLLM server is listed in servers.py (VLLM_SERVERS)")
    answering, lengths = [], []
    for address in addresses:
        url = f"http://{address}/v1/models"
        print(f"Checking {url}...")
        try:
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            cards = {card["id"]: card for card in r.json()["data"]}
        except (requests.exceptions.RequestException, ValueError, KeyError, TypeError) as e:
            print(f"Warning: vLLM server {address} is not ready, it is left out: {e}")
            continue
        if model is None and cards:
            model = next(iter(cards))
        if model not in cards:
            print(f"Warning: vLLM server {address} serves {sorted(cards)}, not {model}: it is left out")
            continue
        answering.append(address)
        if cards[model].get("max_model_len"):
            lengths.append(cards[model]["max_model_len"])
    if not answering:
        sys.exit(f"No vLLM server of {addresses} is ready" + (f" with {model}" if model else ""))
    max_model_len = min(lengths) if lengths else None
    if max_model_len is not None and max_model_len < MIN_MODEL_LEN:
        sys.exit(f"{model} runs with a context of {max_model_len} tokens: restart vLLM with --max-model-len {MIN_MODEL_LEN} or more")
    try:
        probe = _post_chat(
            answering[0],
            {
                "model": model,
                "messages": [{"role": "user", "content": 'How many letters "r" are in "strawberry"? Answer with the JSON {"count": <number>}.'}],
                "max_tokens": 2048,
                "chat_template_kwargs": {"enable_thinking": True},
                "response_format": _json_schema({"type": "object", "properties": {"count": {"type": "integer"}}, "required": ["count"]}),
            },
            timeout_s=300,
        )
        message = probe["choices"][0]["message"]
    except (requests.exceptions.RequestException, ValueError, KeyError, IndexError, TypeError) as e:
        sys.exit(f"vLLM server {answering[0]} does not answer a JSON request: {e}")
    # With a reasoning parser the field is a text, even empty; without one it is null or missing
    reasoning = any(message.get(key) is not None for key in ("reasoning", "reasoning_content"))
    if not reasoning:
        print(f"Warning: {model} does not think before answering (start vLLM with the --reasoning-parser of the model): the second pass will not either")
    return VllmServers(answering, model, max_model_len, reasoning)


def _json_schema(schema: dict) -> dict:
    return {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema}}


def _post_chat(address: str, payload: dict, timeout_s: float) -> dict:
    response = requests.post(f"http://{address}/v1/chat/completions", json=payload, timeout=(10, timeout_s))
    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        raise requests.exceptions.HTTPError(f"{e}\nServer said: {response.text[:2000]}", response=response) from None
    return response.json()


# What a server without a reasoning parser leaves before the answer
_THINKING = re.compile(r"^\s*<think>.*?</think>\s*", re.DOTALL)


def chat_json(
    address: str,
    model: str,
    system: str,
    user: str,
    schema: dict,
    thinking: bool,
    max_tokens: int,
    sampling: dict,
    timeout_s: float = 3600,
) -> Reply:
    """The model's answer to a system and a user message, as a dict following the JSON `schema`."""
    result = _post_chat(
        address,
        {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": max_tokens,
            "chat_template_kwargs": {"enable_thinking": thinking},
            "response_format": _json_schema(schema),
            **sampling,
        },
        timeout_s,
    )
    choice, usage = result["choices"][0], result["usage"]
    answer = None
    content = _THINKING.sub("", choice["message"].get("content") or "")
    # Empty: the model ended its thinking without closing it, so the JSON never started
    if choice.get("finish_reason") != "length" and content.strip():
        try:
            answer = json.loads(content)
        except ValueError:
            raise ValueError(f"Server {address} did not answer in JSON: {content[:500]!r}") from None
        if not isinstance(answer, dict):
            raise ValueError(f"Server {address} answered {content[:500]!r}, not a JSON object")
    return Reply(answer, usage["prompt_tokens"], usage["completion_tokens"], choice.get("finish_reason"))
