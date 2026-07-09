from helpers.pyannote_client import *

from pathlib import Path

from helpers.ffmpeg import *

mp3_path = Path("old_cache/test/audios/france-2_journal-20h00_8550671-edition-du-lundi-22-juin-2026.html.mp3")
wav_path = mp3_path.with_suffix(".wav")


convert_to_16k_mono_wav(mp3_path, wav_path)

# assert_pyannote_servers(["127.0.0.1:8050"])

# result = mp3_to_pyannote_result("127.0.0.1:8050", wav_path)
# print(result)
