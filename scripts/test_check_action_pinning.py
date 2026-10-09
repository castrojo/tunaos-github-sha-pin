#!/usr/bin/env python3
"""Tests for check-action-pinning.py.

The script is dependency-free (no PyYAML), so these tests import it by path and
exercise its parser and CLI directly. Run with either:

    python3 test_check_action_pinning.py
    python3 -m unittest scripts.test_check_action_pinning
"""
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPT_PATH = SCRIPT_DIR / "check-action-pinning.py"

_spec = importlib.util.spec_from_file_location(
    "check_action_pinning", str(SCRIPT_PATH)
)
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)


class TestExternalAction(unittest.TestCase):
    def test_third_party_is_external(self):
        self.assertTrue(check._is_external_action("actions/checkout@v7"))

    def test_namespaced_third_party_is_external(self):
        self.assertTrue(
            check._is_external_action("github/codeql-action/upload-sarif@v3")
        )

    def test_docker_is_not_external(self):
        self.assertFalse(check._is_external_action("docker://alpine:3.19"))

    def test_local_action_is_not_external(self):
        self.assertFalse(check._is_external_action("./.github/actions/setup"))


class TestInternalAction(unittest.TestCase):
    def test_org_internal_action_is_internal(self):
        self.assertTrue(
            check._is_internal_action("tuna-os/.github/.github/actions/ste-lint@main")
        )

    def test_third_party_is_not_internal(self):
        self.assertFalse(check._is_internal_action("actions/checkout@v7"))


class TestShaPinned(unittest.TestCase):
    def test_full_sha_is_pinned(self):
        self.assertTrue(check._is_sha_pinned("3d3c42e5aac5ba805825da76410c181273ba90b1"))

    def test_tag_is_not_pinned(self):
        self.assertFalse(check._is_sha_pinned("v7"))

    def test_branch_is_not_pinned(self):
        self.assertFalse(check._is_sha_pinned("main"))

    def test_empty_ref_is_not_pinned(self):
        self.assertFalse(check._is_sha_pinned(""))

    def test_short_sha_is_not_pinned(self):
        self.assertFalse(check._is_sha_pinned("abc1234"))

    def test_uppercase_hex_is_not_pinned(self):
        # Commit SHAs are lowercase; an uppercase value is not a valid pin.
        self.assertFalse(check._is_sha_pinned("3D3C42E5AAC5BA805825DA76410C181273BA90B1"))


class TestUsesRefs(unittest.TestCase):
    def test_single_uses(self):
        doc = "jobs:\n  build:\n    steps:\n      - uses: actions/checkout@v7\n"
        refs = list(check._uses_refs(doc))
        self.assertEqual(refs, [("actions/checkout@v7", "v7", 4)])

    def test_no_uses(self):
        doc = "name: CI\njobs:\n  build:\n    runs-on: x\n"
        self.assertEqual(list(check._uses_refs(doc)), [])

    def test_commented_uses_is_skipped(self):
        doc = "#   uses: actions/checkout@v7\n"
        self.assertEqual(list(check._uses_refs(doc)), [])

    def test_uses_with_inline_comment(self):
        doc = "      - uses: actions/checkout@v7 # pinned tag\n"
        refs = list(check._uses_refs(doc))
        self.assertEqual(refs, [("actions/checkout@v7", "v7", 1)])

    def test_uses_with_sha_and_comment(self):
        doc = "      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7\n"
        refs = list(check._uses_refs(doc))
        self.assertEqual(refs[0][1], "3d3c42e5aac5ba805825da76410c181273ba90b1")

    def test_job_level_uses_is_caught(self):
        doc = "jobs:\n  ste:\n    uses: owner/repo/.github/workflows/a@main\n"
        refs = list(check._uses_refs(doc))
        self.assertEqual(refs[0][0], "owner/repo/.github/workflows/a@main")

    def test_quoted_value(self):
        doc = '      - uses: "actions/checkout@v7"\n'
        refs = list(check._uses_refs(doc))
        self.assertEqual(refs[0][0], "actions/checkout@v7")
    def test_uses_inside_run_block_is_skipped(self):
        doc = (
            "      - name: Run\n"
            "        run: |\n"
            "          uses: actions/checkout@v7\n"
            "          echo hi\n"
        )
        self.assertEqual(list(check._uses_refs(doc)), [])

    def test_uses_after_run_block_is_caught(self):
        doc = (
            "      - name: Run\n"
            "        run: |\n"
            "          echo hi\n"
            "      - uses: actions/setup-node@v4\n"
        )
        refs = list(check._uses_refs(doc))
        self.assertEqual(refs, [("actions/setup-node@v4", "v4", 4)])

    def test_run_inline_scalar_does_not_start_block(self):
        doc = "        run: echo uses: actions/checkout@v7\n      - uses: actions/checkout@v7\n"
        refs = list(check._uses_refs(doc))
        self.assertEqual(refs, [("actions/checkout@v7", "v7", 2)])


class TestCheckFile(unittest.TestCase):
    def test_tag_is_violation(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "ci.yml").write_text("      - uses: actions/checkout@v7\n")
            violations = check.check_file(Path(d) / "ci.yml")
        self.assertEqual(len(violations), 1)
        self.assertIn("mutable ref", violations[0])

    def test_sha_is_compliant(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "ci.yml").write_text(
                "      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1\n"
            )
            self.assertEqual(check.check_file(Path(d) / "ci.yml"), [])

    def test_internal_action_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "ci.yml").write_text(
                "      - uses: tuna-os/.github/.github/actions/ste-lint@main\n"
            )
            self.assertEqual(check.check_file(Path(d) / "ci.yml"), [])

    def test_local_action_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "ci.yml").write_text("      - uses: ./.github/actions/setup\n")
            self.assertEqual(check.check_file(Path(d) / "ci.yml"), [])

    def test_docker_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "ci.yml").write_text("      - uses: docker://alpine:3.19\n")
            self.assertEqual(check.check_file(Path(d) / "ci.yml"), [])

    def test_multiple_references_reported(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "ci.yml").write_text(
                "      - uses: actions/checkout@v7\n"
                "      - uses: actions/setup-node@v4\n"
            )
            self.assertEqual(len(check.check_file(Path(d) / "ci.yml")), 2)

    def test_mixed_pin_and_tag(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "ci.yml").write_text(
                "      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1\n"
                "      - uses: actions/upload-artifact@v4\n"
            )
            violations = check.check_file(Path(d) / "ci.yml")
        self.assertEqual(len(violations), 1)
        self.assertIn("upload-artifact", violations[0])


class TestFindWorkflowFiles(unittest.TestCase):
    def test_finds_and_sorts_yml_and_yaml(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "b.yml").write_text("")
            (Path(d) / "a.yaml").write_text("")
            (Path(d) / "not-a-workflow.txt").write_text("")
            files = check.find_workflow_files(Path(d))
        self.assertEqual(
            [f.name for f in files],
            ["a.yaml", "b.yml"],
        )

    def test_missing_directory_reports_error(self):
        files = check.find_workflow_files(Path("/no/such/dir/here"))
        self.assertEqual(len(files), 1)
        self.assertIn("no such directory", files[0])


class TestMain(unittest.TestCase):
    def _write_dir(self, **files):
        d = tempfile.mkdtemp()
        for name, content in files.items():
            (Path(d) / name).write_text(content)
        return d

    def test_exit_zero_when_compliant(self):
        d = self._write_dir(
            **{
                "ci.yml": (
                    "      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1\n"
                )
            }
        )
        self.assertEqual(check.main([d]), 0)

    def test_exit_one_when_violation(self):
        d = self._write_dir(**{"ci.yml": "      - uses: actions/checkout@v7\n"})
        self.assertEqual(check.main([d]), 1)

    def test_json_output(self):
        d = self._write_dir(**{"ci.yml": "      - uses: actions/checkout@v7\n"})
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = check.main([d, "--json"])
        self.assertEqual(rc, 1)
        payload = json.loads(buf.getvalue())
        self.assertEqual(len(payload["violations"]), 1)

    def test_quiet_suppresses_output(self):
        d = self._write_dir(**{"ci.yml": "      - uses: actions/checkout@v7\n"})
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = check.main([d, "--quiet"])
        self.assertEqual(rc, 1)
        self.assertEqual(buf.getvalue(), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
