"""Marker-shape regression for replace-images.

Existing replace-images emits `<image sha256="...">...</image>`. After this PR
it should emit `<persisted-image sha256="...">` with the canonical body shape
(Saved to / size / Summary / Preview), so all persist commands share one
contract.

Backward compat: the old `<image sha256=...>` shape must still be readable
(so a session previously processed by old replace-images doesn't break).
"""

from __future__ import annotations

import base64
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent))
from helpers import build_session, iter_persisted_markers, assert_chain_valid


# A 1x1 transparent PNG (smallest valid PNG)
TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAj"
    "CB0C8AAAAASUVORK5CYII="
)


class TestReplaceImagesMarkerShape(unittest.TestCase):

    def setUp(self):
        self.session_path, self.info = build_session([
            ("user", [
                {"type": "text", "text": "look at this:"},
                {"type": "image", "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": TINY_PNG_B64,
                }},
            ]),
            ("assistant", "ok"),
        ])
        # Pre-compute the sha256 of the decoded PNG and write a transcript dir
        import hashlib
        decoded = base64.b64decode(TINY_PNG_B64)
        self.sha256 = hashlib.sha256(decoded).hexdigest()
        self.tdir = Path("/tmp") / "ss-test-img-transcripts"
        self.tdir.mkdir(exist_ok=True)
        (self.tdir / f"{self.sha256}.txt").write_text("a 1x1 transparent png")

    def tearDown(self):
        self.session_path.unlink(missing_ok=True)
        for f in self.tdir.iterdir():
            f.unlink()
        self.tdir.rmdir()

    def test_uses_persisted_image_marker(self):
        from lib.replace_images import replace_images
        replace_images(str(self.session_path), descriptions_dir=str(self.tdir))

        markers = list(iter_persisted_markers(self.session_path))
        kinds = {f["kind"] for *_, f in markers}
        self.assertIn("image", kinds,
                      "expected at least one <persisted-image> marker; got: " + str(kinds))

    def test_sha256_attribute_preserved(self):
        from lib.replace_images import replace_images
        replace_images(str(self.session_path), descriptions_dir=str(self.tdir))

        # Marker text should contain sha256="..." attribute
        markers = list(iter_persisted_markers(self.session_path))
        self.assertTrue(any(self.sha256 in marker_text
                            for *_, marker_text, _ in markers))

    def test_chain_still_valid(self):
        from lib.replace_images import replace_images
        replace_images(str(self.session_path), descriptions_dir=str(self.tdir))
        assert_chain_valid(self.session_path)


if __name__ == "__main__":
    unittest.main()
