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


if __name__ == "__main__":
    unittest.main()
