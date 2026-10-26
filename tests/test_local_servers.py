"""helpers/local_servers.py: the settings a launcher takes from local_servers.yaml, on files of its own.

    python3.11 -m unittest discover tests
"""

import contextlib
import io
import pathlib
import tempfile
import unittest

from helpers.local_servers import launch_settings

DEFAULTS = {"gpus": {0: [8070, 8071]}, "gfx_version": "11.0.0"}


def build_image(gfx_version="11.0.0", ggml_model="large-v3"):
    pass


class LaunchSettingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmp.name) / "local_servers.yaml"

    def tearDown(self):
        self.tmp.cleanup()

    def settings(self, text: str | None, kind: str = "whisper"):
        if text is not None:
            self.path.write_text(text)
        with contextlib.redirect_stdout(io.StringIO()):
            return launch_settings(kind, DEFAULTS, build_image, self.path)

    def assertStops(self, text: str, message: str, kind: str = "whisper"):
        with self.assertRaises(SystemExit) as stop:
            self.settings(text, kind)
        self.assertIn(message, str(stop.exception.code))

    def test_without_the_file_the_launcher_defaults(self):
        self.assertEqual(self.settings(None), ({0: [8070, 8071]}, {"gfx_version": "11.0.0"}))

    def test_the_section_replaces_the_defaults(self):
        text = 'whisper:\n  gpus: {0: [8070, 8071, 8072], 1: [8073]}\n  gfx_version: "12.0.1"\n'
        self.assertEqual(self.settings(text), ({0: [8070, 8071, 8072], 1: [8073]}, {"gfx_version": "12.0.1"}))

    def test_a_default_option_stays_when_the_section_does_not_give_it(self):
        self.assertEqual(self.settings("whisper:\n  gpus:\n    1: [9000]\n"), ({1: [9000]}, {"gfx_version": "11.0.0"}))

    def test_a_kind_without_a_section_or_ports_does_not_run(self):
        self.assertStops("pyannote:\n  gpus: {0: [8050]}\n", "runs no whisper server")
        self.assertStops("whisper:\n  gpus: {}\n", "runs no whisper server")
        self.assertStops("", "runs no whisper server")

    def test_mistakes_stop_before_anything_starts(self):
        self.assertStops("whisper: [\n", "not valid YAML")
        self.assertStops("- whisper\n", "must map each kind")
        self.assertStops('whisper:\n  gpus: {0: [8070]}\n  gfx: "12"\n', "unknown option gfx")
        self.assertStops("whisper:\n  gpus: {0: [8070]}\n  gfx_version: 11.0\n", "gfx_version must be text")
        self.assertStops('whisper:\n  gpus: {0: ["8070"]}\n', "list of port numbers")
        self.assertStops("whisper:\n  gpus: {0: [yes]}\n", "list of port numbers")
        self.assertStops("whisper:\n  gpus: {gpu0: [8070]}\n", "ROCm device number")
        self.assertStops("whisper:\n  gpus: {0: [8070], 1: [8070]}\n", "given twice")
        self.assertStops("whisper:\n  gpus: [8070]\n", "must map ROCm devices")


if __name__ == "__main__":
    unittest.main()
