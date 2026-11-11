"""Token estimates are calibrated to what Claude Code actually measured.

chars/4 is an English rule of thumb. Measured on real sessions it ran 1.16x
low on an English coding session and 1.5x low on a Russian genealogy one
(archive text at ~2.35 chars/token): `analyze` said ~650k for a session CC
measured at 971,681, a strip promised ~300k and would have left ~510k, and the
gauge reset pinned the meter at the low figure until the first real turn.

The newest assistant turn with REAL usage (a Claude Code call always reads or
writes the prompt cache; a strip's gauge reset zeroes those fields) gives the
true size of exactly the prompt it was sent: everything on the chain before
it. Scaling the text estimate by real/estimated fixes the scale per session.
"""

from __future__ import annotations

import contextlib
import io
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import sys
sys.path.insert(0, str(Path(__file__).parent))
from helpers import build_session

import stripper
from lib.chain import (calibration, compute_active_chain_tokens, count_content_chars,
                       estimate_tokens, load_session, reset_usage_metadata, save_session,
                       walk_active_chain)
from lib.fork import read_state, write_state


def _usage(total, cached=True):
    if cached:
        return {"input_tokens": 3, "cache_read_input_tokens": total - 1003,
                "cache_creation_input_tokens": 1000, "output_tokens": 50}
    return {"input_tokens": total, "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0, "output_tokens": 50}


def _session(real_multiple=2.0, dirpath=None):
    """Last assistant turn measured at `real_multiple` x the chars/4 estimate
    of its prompt (everything before it)."""
    path, _ = build_session([
        ("user", "Здравствуйте, " * 300),
        ("assistant", [{"type": "text", "text": "Ответ " * 300}]),
        ("user", "ещё " * 400),
        ("assistant", [{"type": "text", "text": "Готово " * 50}]),
    ])
    if dirpath:
        dst = Path(dirpath) / path.name
        shutil.move(str(path), dst)
        path = dst
    objects = load_session(path)
    prompt = walk_active_chain(objects)[:3]
    est = estimate_tokens(sum(count_content_chars(o)["total"] for o in prompt))
    real = int(est * real_multiple)
    for o in objects:
        if o.get("type") == "assistant":
            o["message"]["model"] = "claude-opus-5-5"
            o["message"]["usage"] = _usage(real)
    save_session(path, objects, create_backup=False)
    return path, est, real


class TestCalibration(unittest.TestCase):
    def setUp(self):
        self._paths = []

    def tearDown(self):
        for p in self._paths:
            p.unlink(missing_ok=True)

    def session(self, *a, **k):
        path, est, real = _session(*a, **k)
        self._paths.append(path)
        return path, est, real

    def test_factor_is_measured_over_estimated(self):
        path, est, real = self.session(2.0)
        cal = calibration(load_session(path))
        self.assertEqual(cal["real"], real)
        self.assertEqual(cal["estimate"], est)
        self.assertAlmostEqual(cal["factor"], real / est, places=6)
        self.assertEqual(cal["pos"], 3)

    def test_estimate_of_a_session_reproduces_its_measurement(self):
        path, est, real = self.session(1.5)
        objects = load_session(path)
        raw = compute_active_chain_tokens(objects)
        calibrated = compute_active_chain_tokens(objects, factor=calibration(objects)["factor"])
        self.assertAlmostEqual(calibrated / raw, 1.5, delta=0.01)

    def test_rewritten_usage_is_not_a_measurement(self):
        path, _, _ = self.session(2.0)
        objects = load_session(path)
        reset_usage_metadata(objects, 1234)  # what a strip's gauge reset leaves
        self.assertIsNone(calibration(objects))

    def test_synthetic_records_are_skipped(self):
        path, _, real = self.session(2.0)
        objects = load_session(path)
        objects[-1]["message"]["model"] = "<synthetic>"
        objects[-1]["message"]["usage"] = _usage(10, cached=False)
        # The newest REAL turn (pos 1) is used instead.
        cal = calibration(objects)
        self.assertEqual(cal["pos"], 1)

    def test_one_response_split_over_records_uses_the_prompt_before_the_first(self):
        path, est, real = self.session(2.0)
        objects = load_session(path)
        last = objects[-1]
        last["message"]["id"] = "msg_split"
        twin = dict(last, uuid="twin-record", parentUuid=last["uuid"])
        twin["message"] = dict(last["message"], content=[{"type": "text", "text": "more"}])
        objects.append(twin)
        cal = calibration(objects)
        self.assertEqual(cal["pos"], 3)
        self.assertEqual(cal["estimate"], est)

    def test_older_turns_do_not_count_once_the_newest_was_rewritten(self):
        # A strip pins only usage above its target; an early, smaller turn
        # keeps real-looking numbers that measured the UNSTRIPPED history.
        path, _, _ = self.session(2.0)
        objects = load_session(path)
        objects[-1]["message"]["usage"] = _usage(500, cached=False)
        self.assertIsNone(calibration(objects))

    def test_factor_is_clamped(self):
        path, _, _ = self.session(50.0)
        self.assertEqual(calibration(load_session(path))["factor"], 3.0)

    def test_images_are_not_scaled(self):
        # factor only multiplies the chars/4 part
        path, _, _ = self.session(2.0)
        objects = load_session(path)
        self.assertEqual(compute_active_chain_tokens(objects, factor=None),
                         compute_active_chain_tokens(objects, factor=1.0))


class TestCalibratedGaugeReset(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="ss-calib-"))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_strip_resets_the_gauge_to_the_calibrated_size(self):
        path, est, real = _session(2.0, dirpath=self.dir)
        args = SimpleNamespace(session=str(path), dry_run=False, no_backup=False,
                               from_pos=0, to_pos=None, fork=False, fork_title=None,
                               in_place=False, new_fork=False, no_usage_reset=False,
                               keep_dropped_thinking=False, func=stripper.cmd_strip_thinking)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            stripper.run_command(args)
        out = buf.getvalue()
        fork = load_session(args._fork_result)
        raw = compute_active_chain_tokens(fork)
        leaf = [o for o in fork if o.get("type") == "assistant"][-1]
        pinned = leaf["message"]["usage"]["input_tokens"]
        self.assertAlmostEqual(pinned / raw, 2.0, delta=0.02)
        self.assertIn("measured at the last API call", out)
        # The fork's own usage is now rewritten, so its calibration lives on in
        # the stripper state for later commands (e.g. reset-usage).
        self.assertIsNone(calibration(fork))
        self.assertAlmostEqual(read_state(args._fork_result)["calibration"]["factor"],
                               2.0, delta=0.01)

    def test_calibration_falls_back_to_fork_state(self):
        path, _, _ = _session(2.0, dirpath=self.dir)
        objects = load_session(path)
        reset_usage_metadata(objects, 1)
        save_session(path, objects, create_backup=False)
        write_state(path, source=path, source_size=1, calibration={"factor": 1.7, "real": 9,
                                                                   "estimate": 5, "pos": 3})
        self.assertEqual(stripper._calibration_of(str(path))["factor"], 1.7)


if __name__ == "__main__":
    unittest.main()
