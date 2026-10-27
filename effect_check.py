#!/usr/bin/env python3
"""agent-effect-check: verify an agent's filesystem effects against a declaration.

Workflow:

  1. snapshot a directory tree before the agent runs
  2. let the agent run
  3. snapshot again and diff the two snapshots
  4. check the observed changes against a declared-effects manifest

Everything is offline and read-only: the tool never executes the agent and
never blocks anything. It reports undeclared changes for a human to review.
Hashing proves that content changed, not who changed it, and only the
snapshotted tree is watched - see README.md for limits.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

ERROR = "error"
WARN = "warning"
INFO = "info"

VALID_EFFECTS = {"create", "modify", "delete"}
OBSERVED_TO_EFFECT = {"added": "create", "changed": "modify", "removed": "delete"}


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def take_snapshot(root: str, excludes: list | None = None) -> dict:
    """Hash every file under root. Symlinks are recorded, not followed."""
    root = os.path.abspath(root)
    excludes = excludes or []
    files: dict[str, dict] = {}
    skipped = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            if any(fnmatch.fnmatch(rel, pat) for pat in excludes):
                continue
            if os.path.islink(full):
                files[rel] = {"link": os.readlink(full)}
                continue
            try:
                files[rel] = {"sha256": sha256_file(full), "size": os.path.getsize(full)}
            except OSError:
                skipped += 1
                print(f"warning: skipped unreadable file {rel}", file=sys.stderr)
    return {
        "version": 1,
        "root": root,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "skipped_unreadable": skipped,
        "files": files,
    }


def diff_snapshots(before: dict, after: dict) -> dict:
    """Observed changes between two snapshots."""
    b, a = before.get("files", {}), after.get("files", {})
    added = sorted(p for p in a if p not in b)
    removed = sorted(p for p in b if p not in a)
    changed = sorted(p for p in a if p in b and a[p] != b[p])
    return {"added": added, "removed": removed, "changed": changed}


def _finding(rule: str, severity: str, path: str, message: str) -> dict:
    return {"rule": rule, "severity": severity, "path": path, "message": message}


def check_effects(before: dict, after: dict, declared: dict) -> list:
    """Reconcile observed changes against a declared-effects manifest."""
    findings: list[dict] = []
    declarations = declared.get("effects", [])
    if not isinstance(declarations, list):
        return [_finding("invalid-manifest", ERROR, "$.effects",
                         "declared manifest needs an effects list")]

    matched = [False] * len(declarations)
    for i, decl in enumerate(declarations):
        if not isinstance(decl, dict) or decl.get("effect") not in VALID_EFFECTS \
                or not isinstance(decl.get("path"), str):
            findings.append(_finding(
                "invalid-declaration", ERROR, f"$.effects[{i}]",
                "each declaration needs effect (create/modify/delete) and a path glob"))

    diff = diff_snapshots(before, after)
    for kind in ("added", "changed", "removed"):
        effect = OBSERVED_TO_EFFECT[kind]
        for path in diff[kind]:
            hit = False
            for i, decl in enumerate(declarations):
                if not isinstance(decl, dict):
                    continue
                if decl.get("effect") == effect and \
                        fnmatch.fnmatch(path, str(decl.get("path", ""))):
                    matched[i] = True
                    hit = True
            if not hit:
                findings.append(_finding(
                    f"undeclared-{effect}", ERROR, path,
                    f"{path} was {effect}d but no declaration covers it"))

    for i, decl in enumerate(declarations):
        if isinstance(decl, dict) and decl.get("effect") in VALID_EFFECTS \
                and not matched[i]:
            findings.append(_finding(
                "declared-not-observed", INFO, str(decl.get("path", "")),
                f'declared {decl.get("effect")} on "{decl.get("path")}" '
                "was not observed; stale declaration or the agent did less than declared"))

    order = {ERROR: 0, WARN: 1, INFO: 2}
    findings.sort(key=lambda f: (order[f["severity"]], f["rule"], f["path"]))
    return findings


def summarize(findings: list) -> dict:
    out = {"errors": 0, "warnings": 0, "infos": 0}
    for f in findings:
        out[{ERROR: "errors", WARN: "warnings", INFO: "infos"}[f["severity"]]] += 1
    out["ok"] = out["errors"] == 0
    return out


def render_text(findings: list, summary: dict) -> str:
    lines = [f"agent-effect-check: {summary['errors']} error(s), "
             f"{summary['warnings']} warning(s), {summary['infos']} info"]
    for f in findings:
        lines.append(f"[{f['severity']}] {f['rule']} at {f['path']}: {f['message']}")
    if not findings:
        lines.append("no findings: every observed change was declared")
    return "\n".join(lines)


def _load_json(path: str) -> dict:
    raw = sys.stdin.read() if path == "-" else open(path, encoding="utf-8").read()
    return json.loads(raw)


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify an agent's filesystem effects against a declaration.")
    sub = parser.add_subparsers(dest="command", required=True)

    snap = sub.add_parser("snapshot", help="hash a directory tree to JSON")
    snap.add_argument("root")
    snap.add_argument("--exclude", action="append", default=[],
                      help="glob of relative paths to skip (repeatable)")

    diffp = sub.add_parser("diff", help="show observed changes between two snapshots")
    diffp.add_argument("before")
    diffp.add_argument("after")

    chk = sub.add_parser("check", help="check observed changes against a declaration")
    chk.add_argument("before")
    chk.add_argument("after")
    chk.add_argument("--declared", required=True,
                     help="JSON manifest with an effects list")
    chk.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)

    try:
        if args.command == "snapshot":
            if not os.path.isdir(args.root):
                print(f"error: not a directory: {args.root}", file=sys.stderr)
                return 2
            print(json.dumps(take_snapshot(args.root, args.exclude), indent=2))
            return 0

        before = _load_json(args.before)
        after = _load_json(args.after)

        if args.command == "diff":
            print(json.dumps(diff_snapshots(before, after), indent=2))
            return 0

        declared = _load_json(args.declared)
        findings = check_effects(before, after, declared)
        summary = summarize(findings)
        if args.json:
            print(json.dumps({"ok": summary["ok"], "summary": summary,
                              "findings": findings}, indent=2))
        else:
            print(render_text(findings, summary))
        return 0 if summary["ok"] else 1
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
