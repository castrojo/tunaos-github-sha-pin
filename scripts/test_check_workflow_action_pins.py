#!/usr/bin/env python3
"""Tests for check-workflow-action-pins.py.

The script is dependency-free (no PyYAML), so these tests import it by path and
exercise its ref parser and CLI directly. Run with either:

    python3 test_check_workflow_action_pins.py
    python3 -m unittest scripts.test_check_workflow_action_pins
"""
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPT_PATH = SCRIPT_DIR / "check-workflow-action-pins.py"

_spec = importlib.util.spec_from_file_location(
    "check_workflow_action_pins", str(SCRIPT_PATH)
)
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)

SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"  # actions/checkout pinned SHA


class TestExtractRef(unittest.TestCase):
    def test_sha_ref(self):
        self.assertEqual(check._extract_ref(f"actions/checkout@{SHA}"), SHA)

    def test_main_ref(self):
        self.assertEqual(
            check._extract_ref("tuna-os/.github/.github/actions/ste-lint@main"), "main"
        )

    def test_version_tag_ref(self):
        self.assertEqual(check._extract_ref("ossf/scorecard-action@v2.4.4"), "v2.4.4")

    def test_relative_ref_has_no_ref(self):
        self.assertIsNone(check._extract_ref("./.github/workflows/reusable-lint.yml"))


class TestStripInlineComment(unittest.TestCase):
    def test_trips_at_space_hash(self):
        self.assertEqual(
            check._strip_inline_comment(f"actions/checkout@{SHA} # v7.0.1"),
            f"actions/checkout@{SHA}",
        )

    def test_leading_hash_has_no_value(self):
        self.assertEqual(check._strip_inline_comment("# v7.0.1"), "")

    def test_no_comment_returns_value(self):
        self.assertEqual(check._strip_inline_comment(f"actions/checkout@{SHA}"), f"actions/checkout@{SHA}")


class TestCheckUsesValue(unittest.TestCase):
    def test_sha_is_compliant(self):
        self.assertIsNone(check.check_uses_value(f"actions/checkout@{SHA}"))

    def test_main_is_violation(self):
        violation = check.check_uses_value("tuna-os/.github/.github/actions/ste-lint@main")
        self.assertIn("@main", violation)

    def test_version_tag_is_violation(self):
        self.assertIn("@v2.4.4", check.check_uses_value("ossf/scorecard-action@v2.4.4"))

    def test_relative_ref_is_compliant(self):
        self.assertIsNone(check.check_uses_value("./.github/workflows/reusable-lint.yml"))

    def test_sha_with_inline_comment_is_compliant(self):
        self.assertIsNone(check.check_uses_value(f"actions/checkout@{SHA} # v7"))


class TestCheckLine(unittest.TestCase):
    def test_list_item_uses(self):
        self.assertIn("@main", check.check_line("      - uses: foo/bar@main"))

    def test_indented_uses(self):
        self.assertIn("@master", check.check_line("        uses: foo/bar@master"))

    def test_comment_line_ignored(self):
        self.assertIsNone(check.check_line("        # uses: foo/bar@main"))

    def test_non_uses_line_ignored(self):
        self.assertIsNone(check.check_line("        runs-on: ubuntu-24.04"))

    def test_sha_line_compliant(self):
        self.assertIsNone(check.check_line(f"        uses: actions/checkout@{SHA}"))


class TestCheckFile(unittest.TestCase):
    def test_flags_only_unpinned_refs(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "ci.yml").write_text(
                f"jobs:\n"
                f"  build:\n"
                f"    uses: actions/checkout@{SHA}\n"
                f"    uses: tuna-os/.github/.github/actions/ste-lint@main\n"
            )
            violations = check.check_file(Path(d) / "ci.yml")
        self.assertEqual(len(violations), 1)
        self.assertIn("ste-lint@main", violations[0])

    def test_compliant_file_reports_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "ci.yml").write_text(f"uses: actions/checkout@{SHA}\n")
            self.assertEqual(check.check_file(Path(d) / "ci.yml"), [])


class TestMain(unittest.TestCase):
    def _write_dir(self, **files):
        d = tempfile.mkdtemp()
        for name, content in files.items():
            (Path(d) / name).write_text(content)
        return d

    def test_exit_zero_when_pinned(self):
        d = self._write_dir(**{"ci.yml": f"uses: actions/checkout@{SHA}\n"})
        self.assertEqual(check.main([d]), 0)

    def test_exit_one_when_unpinned(self):
        d = self._write_dir(**{"ci.yml": "uses: foo/bar@main\n"})
        self.assertEqual(check.main([d]), 1)

    def test_json_output(self):
        d = self._write_dir(**{"ci.yml": "uses: foo/bar@main\n"})
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = check.main([d, "--json"])
        self.assertEqual(rc, 1)
        payload = json.loads(buf.getvalue())
        self.assertEqual(len(payload["violations"]), 1)

    def test_quiet_suppresses_output(self):
        d = self._write_dir(**{"ci.yml": "uses: foo/bar@main\n"})
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = check.main([d, "--quiet"])
        self.assertEqual(rc, 1)
        self.assertEqual(buf.getvalue(), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
