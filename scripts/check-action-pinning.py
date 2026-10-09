#!/usr/bin/env python3
"""Check that every external GitHub Action in a workflow is pinned to a full commit SHA.

tuna-os/.github#176 (recommendation 3b): a workflow that references an action by
a mutable ref (@v7, @main, @tag) lets that action's code change under CI with no
review and no new check -- a supply-chain window. Pinning to a full 40-char
commit SHA freezes the exact code that runs; a new commit needs a new SHA and a
new, reviewable change, so the pinning policy is enforced here in CI.

This is intentionally dependency-free: it parses just enough YAML structure
(`uses:` lines and their indentation) to answer one question -- does each EXTERNAL
action resolve to a full 40-hex-char commit SHA? -- so it runs on GitHub's
ubuntu runners without a `pip install` step. (The permissions check,
check-workflow-permissions.py, answers the other half of the same policy.)

What is NOT checked -- deliberately deferred or out of scope:
  - docker:// actions -- not actions, no action code to pin.
  - ./ local actions -- their code is pinned transitively by the SHA of the
    workflow that calls them (and by the repo they live in).
  - tuna-os/.github/.github/actions/* internal actions -- pinned to @main by
    design (tuna-os/.github#157, the internal-action pinning effort). The org's
    own cross-repo actions stay on @main so callers get the newest version
    automatically; pinning them is a separate, deliberate effort, so they are
    deferred here rather than flagged.

Usage:
    python3 check-action-pinning.py .github/workflows [--json] [--quiet]

Exit code is 0 when every external action is pinned, 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
RUN_BLOCK = re.compile(r"^\s*run:\s*[|>][-+]?\s*$")

DOC_SEPARATOR = re.compile(r"^---\s*$")
USES = re.compile(r'^\s*(?:- )?uses:\s*["\']?([^"\'\s#]+)')
SHA = re.compile(r"^[0-9a-f]{40}$")
# The org's own cross-repo actions, pinned to @main by design (#157).
INTERNAL_ACTION = re.compile(r"^tuna-os/\.github/\.github/actions/")


def _iter_documents(text):
    """Split a YAML stream into document bodies on top-level --- / ... separators."""
    current = []
    for line in text.splitlines():
        if DOC_SEPARATOR.match(line):
            yield "\n".join(current)
            current = []
        else:
            current.append(line)
    yield "\n".join(current)


def _is_external_action(uses):
    """Return True if `uses` references a third-party action that must be pinned.

    docker:// images and ./ local actions are excluded: docker:// is not an
    action, and a local action's code is pinned transitively by the SHA of the
    workflow that calls it.
    """
    return not (uses.startswith("docker://") or uses.startswith("./"))


def _is_internal_action(uses):
    """Return True for tuna-os/.github's own cross-repo actions (deferred)."""
    return bool(INTERNAL_ACTION.match(uses))


def _is_sha_pinned(ref):
    """Return True if `ref` is a full 40-hex-char commit SHA."""
    return bool(SHA.match(ref))


def _uses_refs(document):
    """Yield (uses, ref, line_number) for each action `uses:` line in a document.

    Skips `uses:` that appear inside a `run:` block scalar -- there they are a
    shell command, not an action reference, and flagging them would be a false
    positive.
    """
    in_run_block = False
    run_block_indent = None
    for lineno, raw in enumerate(document.splitlines(), 1):
        stripped = raw.strip()
        if in_run_block:
            # A non-blank line at or above the run: key ends the block scalar.
            if stripped and (len(raw) - len(raw.lstrip(" "))) <= run_block_indent:
                in_run_block = False
                run_block_indent = None
                # Fall through and check this line as a possible uses:.
            else:
                continue
        if RUN_BLOCK.match(raw):
            in_run_block = True
            run_block_indent = len(raw) - len(raw.lstrip(" "))
            continue
        if stripped.startswith("#"):
            continue
        m = USES.match(raw)
        if m is None:
            continue
        uses = m.group(1)
        ref = uses.split("@", 1)[1] if "@" in uses else ""
        yield uses, ref, lineno


def check_file(path):
    """Return a list of violation strings for one workflow file."""
    if not isinstance(path, Path):
        # A non-directory was reported by find_workflow_files; nothing to check.
        return []

    violations = []
    text = path.read_text(encoding="utf-8")
    for document in _iter_documents(text):
        for uses, ref, lineno in _uses_refs(document):
            if not _is_external_action(uses):
                continue
            if _is_internal_action(uses):
                continue
            if _is_sha_pinned(ref):
                continue
            violations.append(
                f"{path}:{lineno}: external action `{uses}` is pinned to "
                f"`{ref or '<none>'}`, a mutable ref -- pin to a full commit SHA"
            )
    return violations


def find_workflow_files(directory):
    """Return the .yml and .yaml files directly inside `directory`, sorted."""
    if not directory.is_dir():
        return [f"{directory}: no such directory"]

    files = sorted(list(directory.glob("*.yml")) + list(directory.glob("*.yaml")))
    return files


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Flag external GitHub Actions not pinned to a full commit SHA."
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
                f"❌ {len(violations)} external action reference(s) not pinned to a commit SHA:"
            )
            for violation in violations:
                print(f"  • {violation}")
            print()
            print("Pin each external action to a full 40-char commit SHA, e.g.:")
            print("  uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7")
        else:
            print("✅ All external actions are pinned to a full commit SHA.")

    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
