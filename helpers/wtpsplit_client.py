import requests
import sys


class IncorrectWtpsplitServer(Exception):
    def __init__(self, address: str, message: str = ""):
        super().__init__(message)
        self.address = address
        self.message = message

    def __str__(self):
        return f"'{self.address}' is not a wtpsplit server" + (
            f":\n{self.message}" if len(self.message) > 0 else "."
        )


def try_wtpsplit_server(address: str) -> bool:
    url = f"http://{address}"
    print(f"Checking {url}...")
    try:
        r = requests.get(url, timeout=5)
        r.raise_for_status()
        if "<h1>wtpsplit.cpp Server</h1>" not in r.text:
            raise IncorrectWtpsplitServer(address)
    except requests.exceptions.RequestException as e:
        raise IncorrectWtpsplitServer(address, str(e))


def assert_wtpsplit_servers(addresses: list):
    for address in addresses:
        try:
            try_wtpsplit_server(address)
        except IncorrectWtpsplitServer as e:
            print(e)
            sys.exit()


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
