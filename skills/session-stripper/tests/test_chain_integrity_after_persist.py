"""Cross-cutting invariant: every persist command must leave the chain valid.

Three rules from the surgery report:
  1. parentUuid chain unbroken back to null
  2. slug consistent across all envelopes
  3. timestamps monotonically non-decreasing

These tests run a representative scenario through every persist command (one
file = one cross-cutting invariant). If any new persist command lands without
respecting the chain, this file catches it without per-command duplication.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent))
from helpers import build_session, assert_chain_valid


def _scenario():
    """A 6-message session with mixed kinds — used by every test below."""
    long = "x" * 3000
    return build_session([
        ("user", "hi"),
        ("assistant", [
            {"type": "text", "text": long},
            {"type": "thinking", "thinking": "deep " * 500},
        ]),
        ("user", "ack"),
        ("assistant", long),
        ("user", "ok"),
        ("assistant", "tail (will be untouched)"),
    ])


class TestChainAfterPersistThinking(unittest.TestCase):
    def test_chain_valid(self):
        from lib.strip_thinking import strip_thinking  # already exists; sanity
        path, _ = _scenario()
        try:
            strip_thinking(str(path), no_backup=True)
            assert_chain_valid(path)
        finally:
            path.unlink(missing_ok=True)


class TestChainAfterPersistText(unittest.TestCase):
    def test_chain_valid(self):
        from lib.persist_text import persist_text_bulk
        path, _ = _scenario()
        try:
            persist_text_bulk(str(path), min_chars=1000, no_backup=True)
            assert_chain_valid(path)
        finally:
            path.unlink(missing_ok=True)


class TestChainAfterPersistMessage(unittest.TestCase):
    def test_chain_valid(self):
        from lib.persist_message import persist_message
        path, _ = _scenario()
        try:
            persist_message(str(path), chain_pos=1, summary="x", no_backup=True)
            assert_chain_valid(path)
        finally:
            path.unlink(missing_ok=True)


class TestChainAfterPersistRange(unittest.TestCase):
    def test_chain_valid(self):
        from lib.persist_range import persist_range
        path, _ = _scenario()
        try:
            persist_range(str(path), from_pos=0, to_pos=4,
                          kinds=("text", "thinking"), min_chars=500,
                          no_backup=True)
            assert_chain_valid(path)
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
