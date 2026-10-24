"""hook-pre-send on Notion tool calls: old text is skipped, lines are paragraphs, short bullets may enumerate."""
import json, os, subprocess, sys, tempfile, unittest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "slopcheck")

FOUR_SENTENCE_PARA = ("Owner team: Chain Execution, led by Jon Amenechi. The team runs the relayer every day. "
                      "It also owns the deposit wallet factory contract. Every change goes through two reviewers first.")
THREE_LINES = ("Owner team: Gamma, led by Pawel Lula.\n"
               "Hagen Henderson wrote the settled-price check last month.\n"
               "Every finding below was read from code on the main branch.\n"
               "We list the open questions at the end of this page.")


def run_hook(tool_name, tool_input):
    home = tempfile.mkdtemp()
    env = dict(os.environ, HOME=home, SLOPCHECK_CONFIG=os.path.join(home, "cfg.json"))
    env.pop("SLOPCHECK_OFF", None); env.pop("SLOPCHECK_INNER", None)
    out = subprocess.run([sys.executable, SCRIPT, "hook-pre-send"], input=json.dumps({"tool_name": tool_name, "tool_input": tool_input}),
                         capture_output=True, text=True, env=env, check=True).stdout.strip()
    return json.loads(out) if out else {}


def denied(res):
    return (res.get("hookSpecificOutput") or {}).get("permissionDecision") == "deny"


NOTION_UPDATE = "mcp__claude_ai_Notion__notion-update-page"


class OldTextSkipped(unittest.TestCase):
    def test_old_str_not_checked(self):
        res = run_hook(NOTION_UPDATE, {"page_id": "abc", "command": "update_content",
                                       "content_updates": [{"old_str": FOUR_SENTENCE_PARA + " Bad — dash.", "new_str": "Owner team: Chain Execution."}]})
        self.assertFalse(denied(res), res)

    def test_selection_with_ellipsis_not_checked(self):
        res = run_hook(NOTION_UPDATE, {"page_id": "abc", "selection_with_ellipsis": FOUR_SENTENCE_PARA + " Bad — dash.", "new_str": "Short."})
        self.assertFalse(denied(res), res)

    def test_new_str_still_checked(self):
        res = run_hook(NOTION_UPDATE, {"page_id": "abc", "command": "update_content",
                                       "content_updates": [{"old_str": "x y", "new_str": "Owner team — Chain Execution, led by Jon."}]})
        self.assertTrue(denied(res), res)


class NotionLinesAreParagraphs(unittest.TestCase):
    def test_notion_lines_split(self):
        res = run_hook(NOTION_UPDATE, {"page_id": "abc", "command": "insert_content", "content": THREE_LINES})
        self.assertFalse(denied(res), res)

    def test_create_pages_content_split(self):
        res = run_hook("mcp__claude_ai_Notion__notion-create-pages", {"pages": [{"properties": {"title": "Gamma notes"}, "content": THREE_LINES}]})
        self.assertFalse(denied(res), res)

    def test_real_long_paragraph_still_denied_in_notion(self):
        res = run_hook(NOTION_UPDATE, {"page_id": "abc", "command": "insert_content", "content": FOUR_SENTENCE_PARA})
        self.assertTrue(denied(res), res)

    def test_slack_keeps_line_joining(self):
        res = run_hook("mcp__claude_ai_Slack__slack_send_message", {"channel_id": "C1", "message": THREE_LINES})
        self.assertTrue(denied(res), res)


class ShortBulletInlineList(unittest.TestCase):
    PAD = "We drafted the plan for the module change this week. It needs a second pass from the team before we send it out.\n\n"

    def test_short_bullet_allowed(self):
        res = run_hook(NOTION_UPDATE, {"page_id": "abc", "command": "insert_content",
                                       "content": self.PAD + "- Add the atomic, incremental, and scalar market types."})
        self.assertFalse(denied(res), res)

    def test_long_bullet_still_denied(self):
        res = run_hook(NOTION_UPDATE, {"page_id": "abc", "command": "insert_content",
                                       "content": self.PAD + "- Add the market types to the module registry before the second audit window closes at the end of next month, atomic, incremental, and scalar."})
        self.assertTrue(denied(res), res)

    def test_paragraph_inline_list_still_denied(self):
        res = run_hook(NOTION_UPDATE, {"page_id": "abc", "command": "insert_content",
                                       "content": self.PAD + "Add the atomic, incremental, and scalar market types."})
        self.assertTrue(denied(res), res)



import importlib.machinery, importlib.util
_loader = importlib.machinery.SourceFileLoader("slopcheck_mod", SCRIPT)
SC = importlib.util.module_from_spec(importlib.util.spec_from_loader("slopcheck_mod", _loader))
_loader.exec_module(SC)

CALLOUT_PAGE = ("<callout icon=\"/icons/description_yellow.svg\" color=\"yellow_bg\">\n"
                "\t### <span color=\"yellow\">Overview</span>\n"
                "\t**Purpose:**\n"
                "\t1. Traders can trade without exposing their strategy on the public chain.\n"
                "</callout>\n"
                "## Specs and Docs {toggle=\"true\"}\n"
                "\t- The <mention-page url=\"https://app.notion.com/p/3f9d\"/> holds the goal.\n"
                "<page url=\"https://app.notion.com/p/3edd\" color=\"blue_bg\">Protocol and oracle modules</page>\n"
                "<empty-block/>")


class NotionMarkupStripped(unittest.TestCase):
    def test_tags_go_text_stays(self):
        out = SC.notion_to_md(CALLOUT_PAGE)
        for gone in ("<callout", "</callout>", "<span", "{toggle", "<mention-page", "<empty-block", "Protocol and oracle modules"):
            self.assertNotIn(gone, out)
        for kept in ("### Overview", "**Purpose:**", "1. Traders can trade", "## Specs and Docs", "holds the goal"):
            self.assertIn(kept, out)

    def test_mention_with_text_keeps_text(self):
        out = SC.notion_to_md("See <mention-page url=\"https://app.notion.com/p/x\">Scalar contracts</mention-page> for the design.")
        self.assertEqual(out, "See Scalar contracts for the design.")

    def test_plain_markdown_unchanged(self):
        md = "A plain paragraph with a <placeholder> in it.\n\n- one bullet"
        self.assertEqual(SC.notion_to_md(md), md)

    def test_callout_page_allowed(self):
        res = run_hook(NOTION_UPDATE, {"page_id": "abc", "command": "replace_content", "new_str": CALLOUT_PAGE})
        self.assertFalse(denied(res), res)

    def test_long_paragraph_inside_callout_still_denied(self):
        res = run_hook(NOTION_UPDATE, {"page_id": "abc", "command": "replace_content",
                                       "new_str": "<callout icon=\"x\" color=\"gray_bg\">\n\t" + FOUR_SENTENCE_PARA + "\n</callout>"})
        self.assertTrue(denied(res), res)


def run_in(home, args, stdin=None):
    env = dict(os.environ, HOME=home, SLOPCHECK_CONFIG=os.path.join(home, "cfg.json"))
    env.pop("SLOPCHECK_OFF", None); env.pop("SLOPCHECK_INNER", None)
    p = subprocess.run([sys.executable, SCRIPT] + args, input=stdin, capture_output=True, text=True, env=env, check=True)
    return p.stdout.strip()


def hook_in(home, tool_name, tool_input):
    out = run_in(home, ["hook-pre-send"], json.dumps({"tool_name": tool_name, "tool_input": tool_input}))
    return json.loads(out) if out else {}


DASHED = {"page_id": "abc", "command": "insert_content", "content": "Owner team — Chain Execution, led by Jon."}


class Override(unittest.TestCase):
    def test_deny_names_what_blocks(self):
        res = run_hook(NOTION_UPDATE, DASHED)
        reason = res["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("Blocking, these alone stop the send:", reason)
        self.assertIn("em-dash or en-dash", reason.split("The rest of the report")[0])
        self.assertIn("slopcheck allow", reason)

    def test_one_override_lets_one_call_through(self):
        home = tempfile.mkdtemp()
        self.assertIn("allowed", run_in(home, ["allow", "--reason", "user said send it"]))
        first = hook_in(home, NOTION_UPDATE, DASHED)
        self.assertFalse(denied(first), first)
        self.assertIn("overridden", first.get("systemMessage", ""))
        second = hook_in(home, NOTION_UPDATE, DASHED)
        self.assertTrue(denied(second), second)
        log = open(os.path.join(home, ".config", "slopcheck", "overrides.jsonl")).read()
        self.assertIn("user said send it", log)

    def test_count_and_clear(self):
        home = tempfile.mkdtemp()
        run_in(home, ["allow", "2"])
        self.assertIn("2 override(s) pending", run_in(home, ["allow", "--status"]))
        self.assertIn("dropped", run_in(home, ["allow", "--clear"]))
        self.assertTrue(denied(hook_in(home, NOTION_UPDATE, DASHED)))

    def test_expired_override_ignored(self):
        home = tempfile.mkdtemp()
        run_in(home, ["allow", "--minutes", "0"])
        self.assertTrue(denied(hook_in(home, NOTION_UPDATE, DASHED)))

    def test_clean_call_does_not_spend_override(self):
        home = tempfile.mkdtemp()
        run_in(home, ["allow"])
        hook_in(home, NOTION_UPDATE, {"page_id": "abc", "command": "insert_content", "content": THREE_LINES})
        self.assertIn("1 override(s) pending", run_in(home, ["allow", "--status"]))

if __name__ == "__main__":
    unittest.main()
