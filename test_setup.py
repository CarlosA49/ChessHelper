"""Regression tests for setup asset validation."""
import io
import os
import tempfile
import unittest
from unittest import mock

import cv2
import numpy as np

import setup


class SetupValidationTests(unittest.TestCase):
    def test_fetch_streams_large_files_and_retries_transient_failures(self):
        payload = b"a" * (2 * 1024 * 1024 + 7)

        class ChunkedResponse(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.close()

        responses = [OSError("temporary failure"), ChunkedResponse(payload)]

        def urlopen(*_args, **_kwargs):
            response = responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

        with tempfile.TemporaryDirectory() as tmp:
            destination = os.path.join(tmp, "download.bin")
            with (mock.patch.object(
                      setup.urllib.request, "urlopen", side_effect=urlopen),
                  mock.patch.object(setup.time, "sleep")):
                setup.fetch("https://example.invalid/file", destination)
            with open(destination, "rb") as downloaded:
                self.assertEqual(downloaded.read(), payload)
            self.assertFalse(os.path.exists(destination + ".part"))

    def test_template_requires_an_alpha_channel(self):
        with tempfile.TemporaryDirectory() as tmp:
            rgb_path = os.path.join(tmp, "rgb.png")
            rgba_path = os.path.join(tmp, "rgba.png")
            cv2.imwrite(rgb_path, np.zeros((8, 8, 3), dtype=np.uint8))
            cv2.imwrite(rgba_path, np.zeros((8, 8, 4), dtype=np.uint8))
            self.assertFalse(setup.valid_template(rgb_path))
            self.assertTrue(setup.valid_template(rgba_path))

    def test_no_piece_sets_is_a_setup_failure(self):
        with (tempfile.TemporaryDirectory() as tmp,
              mock.patch.object(setup, "TEMPLATES_DIR", tmp),
              mock.patch.object(setup, "TEMPLATE_SETS", []),
              mock.patch.object(setup, "ASSET_TEMPLATE_SETS", {})):
            self.assertFalse(setup.setup_templates())


if __name__ == "__main__":
    unittest.main()
