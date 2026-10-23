import requests
import sys


def assert_wtpsplit_servers(addresses: list):
    for address in addresses:
        url = f"http://{address}/health"
        print(f"Checking {url}...")
        try:
            r = requests.get(url, timeout=5)
            r.raise_for_status()
        except requests.exceptions.ReadTimeout:
            # The server's only worker is busy (or still loading the model): requests will wait for it
            print(f"Wtpsplit server {address} is busy or still loading.")
        except requests.exceptions.RequestException as e:
            sys.exit(f"Wtpsplit server {address} is not ready: {e}")


def text_to_sentences(wtpsplit_address: str, text: str) -> list:
    wtpsplit_url = f"http://{wtpsplit_address}/split"
    response = requests.post(
        wtpsplit_url,
        json={
            "text_or_texts": text,
            "verbose": True,
            "split_on_input_newlines": False,
            "weighting": "hat",
            "stride": 32,
            # "max_length": 256
        },
    )
    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        raise requests.exceptions.HTTPError(
            f"{e}\nServer said: {response.text}", response=response
        ) from None
    return response.json()["sentences"]
