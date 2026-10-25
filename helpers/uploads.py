"""Uploads of wav files to the model servers (Whisper, Pyannote, SpeechBrain).

A request can legitimately last hours: the upload, the wait behind other jobs' requests (the
servers run one inference at a time) and the inference of a long VOD. So nothing short limits it:
- TCP keepalive probes notice a machine gone without closing the connection (power loss, network
  cut) in about 3 minutes; a server that is alive answers them however busy it is;
- the timeout is only a backstop for a server process that is stuck (e.g. a GPU hang: the
  connection stays open and /health may still answer).
"""

import os
import socket
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection

_KEEPALIVE = [(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)] + [
    (socket.IPPROTO_TCP, getattr(socket, name), value)
    for name, value in (("TCP_KEEPIDLE", 60), ("TCP_KEEPINTVL", 30), ("TCP_KEEPCNT", 4))
    if hasattr(socket, name)  # Linux names
]


class _KeepaliveAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        kwargs["socket_options"] = HTTPConnection.default_socket_options + _KEEPALIVE
        super().init_poolmanager(*args, **kwargs)


# The timeout of an upload is this plus 3 times the audio duration
TIMEOUT_BASE_S = 24 * 3600


def upload_timeout_s(wav_path: Path) -> float:
    """A 16 kHz mono wav holds 32000 bytes per second."""
    return TIMEOUT_BASE_S + 3 * os.path.getsize(wav_path) / 32000


def post_wav(url: str, wav_path: Path, data: dict) -> requests.Response:
    """POST a wav as the `file` field. One timeout for everything: requests also applies the connect
    timeout to sending the upload, which a busy server may only read much later."""
    with requests.Session() as session, open(wav_path, "rb") as wav_file:
        session.mount("http://", _KeepaliveAdapter())
        return session.post(
            url,
            files={"file": (wav_path.name, wav_file, "audio/wav")},
            data=data,
            timeout=upload_timeout_s(wav_path),
        )
