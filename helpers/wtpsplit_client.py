import requests

from helpers.health import named_model, ready_servers


def assert_wtpsplit_servers(addresses: list, busy_timeout_s: float = 600) -> tuple[str, list[str]]:
    """(model, the listed servers that answer): the others are left out with a warning (see
    helpers/health.py); exits if none does. The servers must all run the same model: it is stored
    with the sentences. A server only answers /health between two requests (it has one thread):
    busy servers are waited for (up to busy_timeout_s) until one answers."""
    return ready_servers("wtpsplit", addresses, "WTPSPLIT_SERVERS", named_model, busy_timeout_s)


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
