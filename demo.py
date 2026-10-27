#!/usr/bin/env python3
"""Demo: snapshot a synthetic project, run a fake 'agent', check its effects.

All data is synthetic and created in a temp directory. Nothing here runs a
real agent - the simulated run just writes files the way an agent might.
"""
import os
import tempfile

from effect_check import take_snapshot, check_effects, summarize, render_text


def write(root, rel, content=b"x"):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(content)


DECLARED = {
    "effects": [
        {"effect": "modify", "path": "src/*.py"},
        {"effect": "create", "path": "report.md"},
        {"effect": "delete", "path": "cache/*.tmp"},
    ]
}


def simulated_agent_run(root, well_behaved):
    write(root, "src/app.py", b"v2")                 # declared modify
    write(root, "report.md", b"summary done")        # declared create
    os.remove(os.path.join(root, "cache", "old.tmp"))  # declared delete
    if not well_behaved:
        write(root, ".env", b"API_KEY=sk-demo")      # UNDECLARED
        write(root, "src/app.py", b"v3 tampered")    # still covered
        os.remove(os.path.join(root, "README.md"))   # UNDECLARED


def scenario(title, well_behaved):
    with tempfile.TemporaryDirectory() as root:
        write(root, "src/app.py", b"v1")
        write(root, "README.md", b"# demo project")
        write(root, "cache/old.tmp", b"stale")

        before = take_snapshot(root)
        simulated_agent_run(root, well_behaved)
        after = take_snapshot(root)

        findings = check_effects(before, after, DECLARED)
        print(f"=== {title} ===")
        print(render_text(findings, summarize(findings)))
        print()


if __name__ == "__main__":
    scenario("well-behaved agent (all effects declared)", well_behaved=True)
    scenario("agent with undeclared side effects", well_behaved=False)
