import requests
import sys
import time


def assert_wtpsplit_servers(addresses: list, busy_timeout_s: float = 600) -> str:
    """Exit if a server is down. Returns the model the servers run (they must all run the same):
    it is stored with the sentences. A server only answers /health between two requests (it has one
    thread): busy servers are waited for (up to busy_timeout_s) until one answers."""
    if not addresses:
        sys.exit("No wtpsplit server is listed in servers.py (WTPSPLIT_SERVERS)")
    models, busy = set(), []
    for address in addresses:
        url = f"http://{address}/health"
        print(f"Checking {url}...")
        try:
            r = requests.get(url, timeout=5)
            r.raise_for_status()
            models.add(r.json()["model"])
        except requests.exceptions.ReadTimeout:
            busy.append(address)  # its only thread is splitting (or the model is still loading)
        except (requests.exceptions.RequestException, ValueError, KeyError) as e:
            sys.exit(f"Wtpsplit server {address} is not ready: {e}")
    deadline = time.monotonic() + busy_timeout_s
    while not models and busy:
        if time.monotonic() > deadline:
            sys.exit(f"No wtpsplit server answered /health within {busy_timeout_s:.0f} s (busy: {busy})")
        print(f"Wtpsplit servers {busy} are busy or loading: waiting for one to answer...", flush=True)
        time.sleep(10)
        for address in busy:
            try:
                r = requests.get(f"http://{address}/health", timeout=5)
                r.raise_for_status()
                models.add(r.json()["model"])
                break
            except requests.exceptions.ReadTimeout:
                pass
            except (requests.exceptions.RequestException, ValueError, KeyError) as e:
                sys.exit(f"Wtpsplit server {address} is not ready: {e}")
    if len(models) > 1:
        sys.exit(f"The wtpsplit servers run different models: {sorted(models)}")
    return models.pop()


def _post_split(wtpsplit_address: str, text_or_texts, timeout_s: float) -> dict:
    response = requests.post(
        f"http://{wtpsplit_address}/split",
        json={
            "text_or_texts": text_or_texts,
            "split_on_input_newlines": False,
            "strip_whitespace": False,  # "".join(sentences) == text: sentences map back to words
            "weighting": "hat",
            "stride": 32,
        },
        timeout=(10, timeout_s),
    )
    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        raise requests.exceptions.HTTPError(
            f"{e}\nServer said: {response.text}", response=response
        ) from None
    return response.json()


def text_to_sentences(wtpsplit_address: str, text: str, timeout_s: float = 3600) -> list[str]:
    return _post_split(wtpsplit_address, text, timeout_s)["sentences"]


def texts_to_sentences(wtpsplit_address: str, texts: list[str], timeout_s: float = 3600) -> tuple[list[list[str]], str]:
    """The sentences of each text, and the model of the server. Each text's sentences joined give
    the text back."""
    result = _post_split(wtpsplit_address, texts, timeout_s)
    sentences = result["sentences"]
    if len(sentences) != len(texts) or any("".join(s) != t for s, t in zip(sentences, texts)):
        raise ValueError(f"Server {wtpsplit_address} returned sentences that do not give the texts back")
    return sentences, result.get("model", "?")
