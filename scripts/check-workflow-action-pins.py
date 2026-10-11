#!/usr/bin/env python3
"""Check that every GitHub Actions workflow pins its references to commit SHAs.

tuna-os/.github#285 (supply-chain hardening): a `uses:` reference that points at
a mutable ref -- a branch (`@main`, `@master`, ...) or a version tag (`@v7.0.1`,
...) -- runs whatever code exists at that ref on the day CI runs. For third-party
actions that means an upstream maintainer (or an attacker who took over the
repo) can change the pinned commit between the PR and the run. For the org's own
reusable actions and workflows it means a push to tuna-os/.github main cascades
into every consumer repo that references it.

The only ref that is safe is an immutable 40-character commit SHA. This script
flags every `uses:` whose ref is anything else, so the org's existing practice of
pinning third-party actions (cde723d) is enforced going forward and the
self-referential internal actions are caught too.

It is intentionally dependency-free: it scans plain text for `uses:` lines so it
runs on the ubuntu runner without a `pip install`. It does not parse YAML, so it
ignores any `uses:` that lives inside a comment or a string value.

Usage:
    python3 check-workflow-action-pins.py .github/workflows [--json] [--quiet]

Exit code is 0 when every reference is pinned to a SHA, 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# A `uses:` key, optionally as a list item (`- uses:`), capturing the value.
USES_RE = re.compile(r"^\s*(?:-\s+)?uses:\s*(?P<value>.+?)\s*$")
# An immutable ref is a full 40-character lowercase hex commit SHA.
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
# A ref is everything after the `@` in a `uses:` value.
REF_RE = re.compile(r"@(?P<ref>.+)$")


def _strip_inline_comment(value):
    """Drop a YAML inline comment from a `uses:` value.

    The reference ends before the first ` #`; anything after is a human note
    (e.g. `# v7.0.1`), not part of the ref. A value that starts with `#` has no
    reference at all.
    """
    if value.lstrip().startswith("#"):
        return ""
    hash_index = value.find(" #")
    return value[:hash_index] if hash_index != -1 else value


def _extract_ref(value):
    """Return the ref part of a `uses:` value, or None if it is unpinned-by-form.

    A relative reference (`./.github/workflows/reusable-lint.yml`) has no `@` and
    is always pinned to the current tree, so it is not a violation.
    """
    if "@" not in value:
        return None
    return REF_RE.search(value).group("ref")


def check_uses_value(value):
    """Return a violation string for one `uses:` value, or None if pinned."""
    ref = _extract_ref(_strip_inline_comment(value))
    if ref is None:
        return None
    if SHA_RE.match(ref):
        return None
    return f"uses reference `{value}` is pinned to mutable ref `@{ref}` -- use a 40-character commit SHA"


def check_line(line):
    """Return a violation string for one workflow line, or None."""
    # Skip comment lines outright; a `uses:` inside a comment is not a real ref.
    if line.lstrip().startswith("#"):
        return None
    match = USES_RE.match(line)
    if match is None:
        return None
    return check_uses_value(match.group("value"))


def check_file(path):
    """Return a list of violation strings for one workflow file."""
    violations = []
    for line in path.read_text(encoding="utf-8").splitlines():
        violation = check_line(line)
        if violation is not None:
            violations.append(violation)
    return violations


def find_workflow_files(directory):
    """Return the .yml and .yaml files directly inside `directory`, sorted."""
    if not directory.is_dir():
        return [f"{directory}: no such directory"]

    files = sorted(list(directory.glob("*.yml")) + list(directory.glob("*.yaml")))
    return files


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Flag GitHub Actions references that are not pinned to a commit SHA."
    )
    parser.add_argument(
        "directory",
        type=Path,
        help="Directory to scan for workflow files (e.g. .github/workflows).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of human-readable text.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Emit no output; exit code alone reports the result.",
    )
    args = parser.parse_args(argv)

    violations = []
    for path in find_workflow_files(args.directory):
        violations.extend(check_file(path))

    if args.json:
        print(json.dumps({"violations": violations}))
        return 1 if violations else 0

    if not args.quiet:
        if violations:
            print(
                "❌ {} GitHub Actions reference(s) not pinned to a commit SHA:".format(
                    len(violations)
                )
            )
            for violation in violations:
                print(f"  • {violation}")
            print()
            print("Pin each reference to the immutable 40-character SHA of the commit")
            print("you want to run, e.g.:")
            print("  uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1")
        else:
            print("✅ All GitHub Actions references are pinned to commit SHAs.")

    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
