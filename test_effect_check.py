import json
import os
import unittest

import effect_check as ec


class SnapTestCase(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.td = tempfile.TemporaryDirectory()
        self.root = self.td.name

    def tearDown(self):
        self.td.cleanup()

    def write(self, rel, content=b"x"):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(content)

    def remove(self, rel):
        os.remove(os.path.join(self.root, rel))

    def snap(self, **kw):
        return ec.take_snapshot(self.root, **kw)


class TestSnapshot(SnapTestCase):
    def test_hashes_files_with_relpaths(self):
        self.write("a.txt", b"hello")
        self.write("sub/b.txt", b"world")
        snap = self.snap()
        self.assertEqual(set(snap["files"]), {"a.txt", os.path.join("sub", "b.txt")})
        self.assertEqual(len(snap["files"]["a.txt"]["sha256"]), 64)

    def test_deterministic_for_same_content(self):
        self.write("a.txt", b"same")
        self.assertEqual(self.snap()["files"], self.snap()["files"])

    def test_content_change_changes_hash(self):
        self.write("a.txt", b"v1")
        first = self.snap()
        self.write("a.txt", b"v2")
        self.assertNotEqual(first["files"]["a.txt"], self.snap()["files"]["a.txt"])

    def test_exclude_glob(self):
        self.write("keep.txt")
        self.write("debug.log")
        snap = self.snap(excludes=["*.log"])
        self.assertEqual(set(snap["files"]), {"keep.txt"})

    def test_symlink_recorded_not_followed(self):
        self.write("target.txt", b"data")
        os.symlink(os.path.join(self.root, "target.txt"),
                   os.path.join(self.root, "link.txt"))
        snap = self.snap()
        self.assertIn("link", snap["files"]["link.txt"])
        self.assertNotIn("sha256", snap["files"]["link.txt"])


class TestDiff(SnapTestCase):
    def test_added_removed_changed(self):
        self.write("stay.txt", b"v1")
        self.write("gone.txt")
        before = self.snap()
        self.write("stay.txt", b"v2")
        self.write("new.txt")
        self.remove("gone.txt")
        after = self.snap()
        diff = ec.diff_snapshots(before, after)
        self.assertEqual(diff["added"], ["new.txt"])
        self.assertEqual(diff["removed"], ["gone.txt"])
        self.assertEqual(diff["changed"], ["stay.txt"])

    def test_no_changes_empty_diff(self):
        self.write("a.txt")
        snap = self.snap()
        diff = ec.diff_snapshots(snap, self.snap())
        self.assertEqual(diff, {"added": [], "removed": [], "changed": []})


def manifest(*effects):
    return {"effects": [dict(effect=e, path=p) for e, p in effects]}


class TestCheck(SnapTestCase):
    def setUp(self):
        super().setUp()
        self.write("src/app.py", b"v1")
        self.write("notes.txt", b"old")
        self.before = self.snap()

    def run_agent(self):
        self.write("src/app.py", b"v2")        # declared modify
        self.write("report.md", b"done")       # declared create
        self.remove("notes.txt")               # declared delete

    def test_all_declared_passes(self):
        self.run_agent()
        declared = manifest(("modify", "src/*.py"), ("create", "report.md"),
                            ("delete", "notes.txt"))
        findings = ec.check_effects(self.before, self.snap(), declared)
        self.assertEqual(ec.summarize(findings)["errors"], 0, findings)

    def test_undeclared_create_is_error(self):
        self.write(".env", b"SECRET=1")
        findings = ec.check_effects(self.before, self.snap(), manifest())
        rules = [f["rule"] for f in findings]
        self.assertIn("undeclared-create", rules)
        self.assertFalse(ec.summarize(findings)["ok"])

    def test_undeclared_modify_and_delete(self):
        self.write("src/app.py", b"v2")
        self.remove("notes.txt")
        findings = ec.check_effects(self.before, self.snap(), manifest())
        rules = {f["rule"] for f in findings}
        self.assertEqual(rules, {"undeclared-modify", "undeclared-delete"})

    def test_wrong_effect_type_does_not_match(self):
        self.write("src/app.py", b"v2")
        declared = manifest(("create", "src/*.py"))  # declared create, observed modify
        findings = ec.check_effects(self.before, self.snap(), declared)
        self.assertIn("undeclared-modify", [f["rule"] for f in findings])

    def test_declared_not_observed_is_info(self):
        declared = manifest(("create", "never-written.txt"))
        findings = ec.check_effects(self.before, self.snap(), declared)
        self.assertIn("declared-not-observed", [f["rule"] for f in findings])
        self.assertTrue(ec.summarize(findings)["ok"])  # info does not fail

    def test_glob_covers_multiple_paths(self):
        self.write("out/a.txt")
        self.write("out/b.txt")
        declared = manifest(("create", "out/*"))
        findings = ec.check_effects(self.before, self.snap(), declared)
        self.assertEqual(ec.summarize(findings)["errors"], 0, findings)

    def test_invalid_declaration_is_error(self):
        findings = ec.check_effects(self.before, self.snap(),
                                    {"effects": [{"effect": "explode", "path": "x"}]})
        self.assertIn("invalid-declaration", [f["rule"] for f in findings])

    def test_invalid_manifest_shape(self):
        findings = ec.check_effects(self.before, self.snap(), {"effects": "nope"})
        self.assertIn("invalid-manifest", [f["rule"] for f in findings])


class TestCLI(SnapTestCase):
    def test_end_to_end_exit_codes(self):
        self.write("ok.txt", b"v1")
        before_path = os.path.join(self.root, "before.json")
        after_path = os.path.join(self.root, "after.json")
        decl_path = os.path.join(self.root, "declared.json")

        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(ec.main(["snapshot", self.root,
                                      "--exclude", "*.json"]), 0)
        with open(before_path, "w") as fh:
            fh.write(buf.getvalue())

        self.write("ok.txt", b"v2")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ec.main(["snapshot", self.root, "--exclude", "*.json"])
        with open(after_path, "w") as fh:
            fh.write(buf.getvalue())

        with open(decl_path, "w") as fh:
            json.dump(manifest(("modify", "ok.txt")), fh)
        self.assertEqual(ec.main(["check", before_path, after_path,
                                  "--declared", decl_path]), 0)

        with open(decl_path, "w") as fh:
            json.dump(manifest(), fh)
        self.assertEqual(ec.main(["check", before_path, after_path,
                                  "--declared", decl_path]), 1)
        self.assertEqual(ec.main(["check", "missing.json", after_path,
                                  "--declared", decl_path]), 2)


if __name__ == "__main__":
    unittest.main()
