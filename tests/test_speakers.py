"""helpers/speakers.py: grouping the voices that sound alike, on made-up vectors.

    python3.11 -m unittest discover tests
"""

import unittest

import numpy as np

from helpers.speakers import Voice, group_voices


def voice(direction: list, seconds: float, noise: float = 0.0, seed: int = 0) -> Voice:
    vector = np.array(direction, dtype=np.float32) + noise * np.random.default_rng(seed).standard_normal(len(direction)).astype(np.float32)
    return Voice(vector / np.linalg.norm(vector), seconds, 1)


class GroupVoicesTest(unittest.TestCase):
    def test_the_same_voice_in_several_contents_is_one_group(self):
        host, guest = [1, 0, 0, 0], [0, 1, 0, 0]
        voices = {
            (1, "SPEAKER_00"): voice(host, 600, 0.1, 1),
            (2, "SPEAKER_01"): voice(host, 300, 0.1, 2),
            (3, "SPEAKER_00"): voice(host, 900, 0.1, 3),
            (2, "SPEAKER_00"): voice(guest, 120, 0.1, 4),
            (3, "SPEAKER_02"): voice(guest, 60, 0.1, 5),
            (4, "SPEAKER_00"): voice([0, 0, 1, 0], 30),
        }
        groups = group_voices(voices, 0.6)
        keys = [sorted(key for key, _, _ in members) for _, members in groups]
        self.assertEqual(keys, [[(1, "SPEAKER_00"), (2, "SPEAKER_01"), (3, "SPEAKER_00")], [(2, "SPEAKER_00"), (3, "SPEAKER_02")], [(4, "SPEAKER_00")]])
        self.assertEqual(groups[0][0].seconds, 1800)
        self.assertAlmostEqual(float(np.linalg.norm(groups[0][0].vector)), 1.0, places=5)
        self.assertEqual(groups[0][1][0][0], (3, "SPEAKER_00"))  # the longest first

    def test_a_voice_joins_by_its_similarity_to_the_first_voice_of_the_group(self):
        # c is close to b but not to a, the first (longest) voice: identification of c from a's
        # samples would not name it, so it must not join a's group
        a, b, c = voice([1, 0], 100), voice([1, 1], 50), voice([0.3, 1], 10)
        groups = group_voices({"a": a, "b": b, "c": c}, 0.7)
        self.assertEqual([[key for key, _, _ in members] for _, members in groups], [["a", "b"], ["c"]])
        self.assertAlmostEqual(groups[0][1][1][2], float(a.vector @ b.vector), places=5)

    def test_below_the_threshold_voices_stay_apart(self):
        voices = {"a": voice([1, 0], 10), "b": voice([1, 1], 5)}  # similarity 0.71
        self.assertEqual(len(group_voices(voices, 0.8)), 2)
        self.assertEqual(len(group_voices(voices, 0.7)), 1)

    def test_no_voice_no_group(self):
        self.assertEqual(group_voices({}, 0.6), [])


if __name__ == "__main__":
    unittest.main()
