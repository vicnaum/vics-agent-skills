"""The structure layer: scripts/structcheck in its venv, and slopcheck calling it. Skipped when the venv is not installed."""
import json, os, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SLOPCHECK = os.path.join(HERE, "..", "scripts", "slopcheck")
STRUCTCHECK = os.path.join(HERE, "..", "scripts", "structcheck")
VENV = os.path.expanduser(os.environ.get("SLOPCHECK_STRUCTURE_VENV", "~/.local/share/slopcheck/venv"))
PY = os.path.join(VENV, "bin", "python")
HAVE = os.path.exists(PY)

GARDEN = "Items this work needs ship now, while scalar-only items are hints for Q1 with no owners and no tickets."


def struct(text, *args):
    p = subprocess.run([PY, STRUCTCHECK, "--stdin", "--json", *args], input=text, capture_output=True, text=True)
    assert p.returncode in (0, 1, 2), p.stderr
    return json.loads(p.stdout)


def rules(text, *args):
    return {b["rule"] for b in struct(text, *args)["breaks"]}


def hard_rules(text):
    return {b["rule"] for b in struct(text)["breaks"] if b["severity"] == "hard"}


@unittest.skipUnless(HAVE, "structure venv not installed (slopcheck install-structure)")
class Rules(unittest.TestCase):
    def test_garden_path_sentence_has_no_wellformed_parse(self):
        self.assertIn("parse-failure", rules(GARDEN))

    def test_plain_sentence_has_no_hard_break(self):
        self.assertEqual(hard_rules("The team ships the two items this week, and the rest of the list waits."), set())

    def test_verbless_sentence(self):
        self.assertIn("no-verb", rules("With the second release, four more rows for the same table."))

    def test_colon_label_is_not_a_verbless_sentence(self):
        self.assertNotIn("no-verb", rules("Gate for the second step: none of the rows above."))

    def test_appositive_between_subject_and_verb(self):
        self.assertIn("appositive-wedged", rules("The redeem drains, the SQL steps that turn events into activity rows, save no asset."))

    def test_passive_is_advisory(self):
        r = struct("The producer is fed by the listeners every night of the week.")
        passives = [b for b in r["breaks"] if b["rule"] == "passive"]
        self.assertTrue(passives)
        self.assertEqual(passives[0]["severity"], "advisory")

    def test_headings_and_tables_are_skipped(self):
        text = "# With the second release four more rows\n\n| With the second release | four more rows |\n"
        self.assertEqual(rules(text), set())

    def test_long_verbless_bullet_is_checked(self):
        self.assertIn("no-verb", rules("- With the second release, four more rows for the same table and two more columns for the index page.\n"))

    def test_short_noun_phrase_bullet_is_allowed(self):
        self.assertNotIn("no-verb", rules("- instruction written as a statement of fact\n"))

    def test_quoted_span_is_a_mention(self):
        self.assertNotIn("fragment", rules('Then "now" arrives and "Items" has no verb, so the reader backs up and reparses.'))

    def test_one_word_appositive_is_allowed(self):
        self.assertNotIn("appositive-wedged", rules("Our teammate Bob writes the release note for the proxy upgrade every week."))

    def test_bold_lead_line_may_be_verbless(self):
        text = "**Model layer, off by default.** The small model reads the text and returns the terms it did not know.\n"
        self.assertNotIn("no-verb", rules(text))

    def test_code_spans_and_links_are_dropped(self):
        text = "The command `slopcheck install` writes the hooks into [the settings file](https://example.com/x) and backs it up first."
        self.assertNotIn("no-verb", rules(text))

    def test_exit_code_two_on_hard_break(self):
        p = subprocess.run([PY, STRUCTCHECK, "--stdin"], input=GARDEN, capture_output=True, text=True)
        self.assertEqual(p.returncode, 2, p.stdout)


@unittest.skipUnless(HAVE, "structure venv not installed (slopcheck install-structure)")
class ThroughSlopcheck(unittest.TestCase):
    def env(self, **cfg):
        home = tempfile.mkdtemp()
        path = os.path.join(home, "cfg.json")
        base = {"structure_check": True, "structure_venv": VENV, "structure_surprisal": False}
        base.update(cfg)
        with open(path, "w") as f:
            json.dump(base, f)
        e = dict(os.environ, HOME=home, SLOPCHECK_CONFIG=path)
        e.pop("SLOPCHECK_OFF", None); e.pop("SLOPCHECK_INNER", None)
        return e

    def test_json_result_carries_structure(self):
        p = subprocess.run([sys.executable, SLOPCHECK, "--stdin", "--json", "--surface", "doc"], input=GARDEN, capture_output=True, text=True, env=self.env())
        r = json.loads(p.stdout)
        self.assertIn("parse-failure", {b["rule"] for b in r["structure"]["breaks"]})
        self.assertEqual(p.returncode, 1)   # reported, not a rule break: nothing in structure_deny

    def test_exit_code_two_when_rule_is_in_structure_deny(self):
        p = subprocess.run([sys.executable, SLOPCHECK, "--stdin", "--json", "--surface", "doc"], input=GARDEN, capture_output=True, text=True, env=self.env(structure_deny=["parse-failure"]))
        self.assertEqual(p.returncode, 2)

    def test_imperative_is_not_a_fragment(self):
        self.assertNotIn("fragment", rules("Before any message over 150 words, pipe the draft through the checker and fix what it names."))

    def test_off_by_default(self):
        p = subprocess.run([sys.executable, SLOPCHECK, "--stdin", "--json"], input=GARDEN, capture_output=True, text=True, env=self.env(structure_check=False))
        r = json.loads(p.stdout)
        self.assertEqual(r["structure"].get("skipped"), "structure_check off")

    def test_outward_send_denied_when_rule_is_in_structure_deny(self):
        text = GARDEN + " The rest of the plan waits for the review next week, and nobody has written the tickets for it yet."
        p = subprocess.run([sys.executable, SLOPCHECK, "hook-pre-send"], input=json.dumps({"tool_name": "mcp__claude_ai_Slack__slack_send_message", "tool_input": {"channel_id": "C1", "text": text}}),
                           capture_output=True, text=True, env=self.env(structure_deny=["parse-failure"]))
        res = json.loads(p.stdout)
        self.assertEqual((res.get("hookSpecificOutput") or {}).get("permissionDecision"), "deny", res)
        self.assertIn("parse-failure", res["hookSpecificOutput"]["permissionDecisionReason"])

    def test_default_only_reports_on_outward_send(self):
        text = GARDEN + " The rest of the plan waits for the review next week, and nobody has written the tickets for it yet."
        p = subprocess.run([sys.executable, SLOPCHECK, "hook-pre-send"], input=json.dumps({"tool_name": "mcp__claude_ai_Slack__slack_send_message", "tool_input": {"channel_id": "C1", "text": text}}),
                           capture_output=True, text=True, env=self.env())
        res = json.loads(p.stdout)
        self.assertNotEqual((res.get("hookSpecificOutput") or {}).get("permissionDecision"), "deny", res)
        self.assertIn("parse failure", res.get("systemMessage", ""), res)

    def test_advisory_rule_does_not_deny(self):
        text = "The producer is fed by the listeners every night of the week. Each listener reads one queue and writes one table for the next day."
        p = subprocess.run([sys.executable, SLOPCHECK, "hook-pre-send"], input=json.dumps({"tool_name": "mcp__claude_ai_Slack__slack_send_message", "tool_input": {"channel_id": "C1", "text": text}}),
                           capture_output=True, text=True, env=self.env())
        res = json.loads(p.stdout) if p.stdout.strip() else {}
        self.assertNotEqual((res.get("hookSpecificOutput") or {}).get("permissionDecision"), "deny", res)


if __name__ == "__main__":
    unittest.main()
