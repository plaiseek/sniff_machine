from helpers.wtpsplit_client import *
from helpers.subtitles import *

text = load_sparsified_transcription("old_cache/test/transcripts/OZ4TjIIwOaE.fr.txt", 0)
sentences = text_to_sentences("127.0.0.1:8060", text.replace("\n", " ").replace(",", " ").replace(".", " ").replace("  ", " ").replace("  ", " ").lower())

for sentence in sentences:
    print(sentence)

print(len(sentences))