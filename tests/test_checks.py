"""Unit tests for rendering and structural checks (no filesystem agents)."""
import unittest

from agentloom.checks import (
    compose_required_vars,
    env_file_vars,
    parse_frontmatter,
    validate_cron,
)
from agentloom.render import TemplateError, render_text


class RenderTests(unittest.TestCase):
    def test_replaces_known_placeholders(self):
        out = render_text("name={{AGENT_NAME}} port={{PORT_AGENT}}",
                          {"AGENT_NAME": "x", "PORT_AGENT": 4170})
        self.assertEqual(out, "name=x port=4170")

    def test_unknown_placeholder_raises(self):
        with self.assertRaises(TemplateError):
            render_text("{{NOPE}}", {"AGENT_NAME": "x"})

    def test_tolerates_whitespace_inside_placeholder(self):
        out = render_text("{{ AGENT_NAME }}", {"AGENT_NAME": "y"})
        self.assertEqual(out, "y")


class CronCheckTests(unittest.TestCase):
    def test_valid_expressions(self):
        for expr in ("* * * * *", "*/5 * * * *", "0 9 * * 1-5",
                     "1,15,30 2-4 1 1 0", "59 23 31 12 6"):
            ok, msg = validate_cron(expr)
            self.assertTrue(ok, f"{expr} should be valid: {msg}")

    def test_invalid_expressions(self):
        for expr in ("", "* * * *", "60 * * * *", "* 24 * * *",
                     "* * 0 * *", "* * * 13 *", "* * * * 7",
                     "a * * * *", "5-1 * * * *", "*/0 * * * *"):
            ok, _ = validate_cron(expr)
            self.assertFalse(ok, f"{expr!r} should be invalid")


class FrontmatterTests(unittest.TestCase):
    def test_parses_flat_frontmatter(self):
        text = "---\nname: my-skill\ndescription: does things\n---\n\n# Body\n"
        fm = parse_frontmatter(text)
        self.assertEqual(fm["name"], "my-skill")
        self.assertEqual(fm["description"], "does things")

    def test_no_frontmatter_returns_none(self):
        self.assertIsNone(parse_frontmatter("# just markdown"))

    def test_quoted_values_unquoted(self):
        fm = parse_frontmatter("---\nname: 'x'\ndescription: \"y\"\n---\n")
        self.assertEqual(fm["name"], "x")
        self.assertEqual(fm["description"], "y")


class ComposeEnvTests(unittest.TestCase):
    def test_vars_without_defaults_are_required(self):
        compose = "x: ${TOKEN}\ny: ${PORT:-8000}\nz: ${NAME?err}"
        required = compose_required_vars(compose)
        self.assertIn("TOKEN", required)
        self.assertNotIn("PORT", required)
        self.assertNotIn("NAME", required)  # ?-form has a behaviour, not required

    def test_env_file_vars(self):
        text = "# comment\nTOKEN=\nexport OTHER=1\nnot a var line\n"
        self.assertEqual(env_file_vars(text), {"TOKEN", "OTHER"})


if __name__ == "__main__":
    unittest.main()
