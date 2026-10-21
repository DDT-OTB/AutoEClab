"""Versioned experiment-file operations use only temporary synthetic files."""
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_api
import test_auth


class ExperimentFileTests(unittest.TestCase):
    setUp = test_api.ApiTests.setUp
    tearDown = test_api.ApiTests.tearDown
    request = test_api.ApiTests.request

    def source(self, with_notes=True):
        name = self.sequence["id"]
        _, record = self.request("GET", "/api/experiments/" + name)
        if with_notes:
            self.request("POST", "/api/experiments", {"sequence": self.sequence, "expected_version": record["version"]})
            companion = self.lab / "config/sequences" / (name + ".md")
            companion.write_text(companion.read_text() + "\nManual notes to retain.\n")
        return name, self.request("GET", "/api/experiments/" + name)[1]

    def test_catalog_reports_file_metadata_and_keeps_invalid_files_visible(self):
        broken = self.lab / "config/sequences/broken.json"
        broken.write_text("{ invalid json\n")
        _, catalog = self.request("GET", "/api/experiments")
        records = {item["id"]: item for item in catalog["experiments"]}
        self.assertTrue(records[self.sequence["id"]]["editable"])
        self.assertGreater(records[self.sequence["id"]]["size_bytes"], 0)
        self.assertEqual(records[self.sequence["id"]]["filename"], self.sequence["id"] + ".json")
        self.assertFalse(records["broken"]["editable"])
        self.assertTrue(records["broken"]["file_error"])
        code, content = self.request("GET", "/api/experiments/broken/content")
        self.assertEqual(code, 200)
        self.assertEqual(content["content"], broken.read_text())
        self.assertEqual(content["version"], hashlib.sha256(broken.read_bytes()).hexdigest())

    def test_rename_updates_json_and_companion_and_backs_up_both(self):
        name, record = self.source()
        path = self.lab / "config/sequences" / (name + ".json")
        original, notes = path.read_bytes(), path.with_suffix(".md").read_bytes()
        code, result = self.request("POST", "/api/experiments/" + name + "/rename", {"new_id": "renamed_demo", "expected_version": record["version"]})
        self.assertEqual(code, 200)
        self.assertTrue(result["renamed"])
        self.assertEqual(result["sequence"]["id"], "renamed_demo")
        self.assertFalse(path.exists())
        self.assertFalse(path.with_suffix(".md").exists())
        target = path.with_name("renamed_demo.json")
        self.assertEqual(json.loads(target.read_text())["steps"], self.sequence["steps"])
        self.assertIn("Manual notes to retain.", target.with_suffix(".md").read_text())
        self.assertIn("# renamed_demo", target.with_suffix(".md").read_text())
        self.assertIn("方案：renamed_demo", target.with_suffix(".md").read_text())
        backup = self.root / "data/backups" / result["backup_id"]
        self.assertEqual((backup / path.name).read_bytes(), original)
        self.assertEqual((backup / path.with_suffix(".md").name).read_bytes(), notes)
        self.assertEqual(json.loads((backup / "operation.meta.json").read_text())["action"], "rename")

    def test_duplicate_preserves_the_source_and_never_overwrites_a_destination(self):
        name, record = self.source()
        path = self.lab / "config/sequences" / (name + ".json")
        original, notes = path.read_bytes(), path.with_suffix(".md").read_bytes()
        body = {"new_id": "copied_demo", "expected_version": record["version"]}
        code, result = self.request("POST", "/api/experiments/" + name + "/duplicate", body)
        self.assertEqual(code, 200)
        self.assertTrue(result["copied"])
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(path.with_suffix(".md").read_bytes(), notes)
        self.assertEqual(result["sequence"]["id"], "copied_demo")
        self.assertEqual(self.request("POST", "/api/experiments/" + name + "/rename", body)[0], 409)
        orphan = path.with_name("orphan.md")
        orphan.write_text("Unpaired manual document")
        self.assertEqual(self.request("POST", "/api/experiments/" + name + "/duplicate", {**body, "new_id": "orphan"})[0], 409)
        self.assertEqual(orphan.read_text(), "Unpaired manual document")

    def test_mutations_reject_missing_or_stale_versions_without_modifying_files(self):
        name, record = self.source()
        path = self.lab / "config/sequences" / (name + ".json")
        path.write_bytes(path.read_bytes() + b"\n")
        for action in ("rename", "duplicate"):
            code, body = self.request("POST", "/api/experiments/" + name + "/" + action, {"new_id": "other_demo", "expected_version": record["version"]})
            self.assertEqual((code, body["error"]), (409, "version_conflict"))
        self.assertEqual(self.request("DELETE", "/api/experiments/" + name, {"expected_version": record["version"]})[0], 409)
        self.assertEqual(self.request("DELETE", "/api/experiments/" + name, {})[0], 400)
        self.assertTrue(path.exists())
        self.assertFalse(path.with_name("other_demo.json").exists())

    def test_delete_removes_the_pair_only_after_a_backup_and_can_delete_invalid_json(self):
        path = self.lab / "config/sequences/broken.json"
        path.write_text("{ still invalid")
        path.with_suffix(".md").write_text("Personal explanation")
        _, record = self.request("GET", "/api/experiments/broken/content")
        code, result = self.request("DELETE", "/api/experiments/broken", {"expected_version": record["version"]})
        self.assertEqual(code, 200)
        self.assertTrue(result["deleted"])
        self.assertFalse(path.exists())
        self.assertFalse(path.with_suffix(".md").exists())
        backup = self.root / "data/backups" / result["backup_id"]
        self.assertEqual((backup / "broken.json").read_text(), "{ still invalid")
        self.assertEqual((backup / "broken.md").read_text(), "Personal explanation")
        self.assertEqual(self.request("GET", "/api/experiments/broken/content")[0], 404)

    def test_file_operations_enforce_admin_csrf_and_authentication(self):
        name, record = self.source()
        self.auth.create_user("viewer", "viewertest123", role="viewer", temporary=False)
        token, session = self.auth.login("viewer", "viewertest123", "127.0.0.1")
        request = lambda method, path, body=None, **kw: test_auth.AuthenticationTests.request(self, method, path, body, **kw)
        self.assertEqual(request("GET", "/api/experiments/" + name + "/content", token=token)[0], 200)
        for method, route, body in (("POST", "/api/experiments/" + name + "/rename", {"new_id": "forged", "expected_version": record["version"]}),
                ("POST", "/api/experiments/" + name + "/duplicate", {"new_id": "forged", "expected_version": record["version"]}),
                ("DELETE", "/api/experiments/" + name, {"expected_version": record["version"]})):
            extra = {"X-Role": "admin", "X-Pump-Console": "1"}
            self.assertEqual(request(method, route, body, token=token, csrf=session["csrf_token"], extra=extra)[0], 403)
            self.assertEqual(request(method, route, body, extra=extra)[0], 401)
            self.assertEqual(request(method, route, body, token=self.token, extra=extra)[0], 403)

    def test_symlinks_and_traversal_are_never_used_for_file_mutations(self):
        name, record = self.source(False)
        outside = self.root / "outside.json"
        outside.write_text("External file")
        alias = self.lab / "config/sequences/alias.json"
        alias.symlink_to(outside)
        self.assertEqual(self.request("GET", "/api/experiments/alias/content")[0], 400)
        path = self.lab / "config/sequences" / (name + ".json")
        path.with_suffix(".md").symlink_to(outside)
        code, _ = self.request("POST", "/api/experiments/" + name + "/rename", {"new_id": "renamed", "expected_version": record["version"]})
        self.assertEqual(code, 400)
        self.assertEqual(self.request("DELETE", "/api/experiments/" + name, {"expected_version": record["version"]})[0], 400)
        self.assertEqual(outside.read_text(), "External file")
        self.assertEqual(self.request("POST", "/api/experiments/" + name + "/rename", {"new_id": "../outside", "expected_version": record["version"]})[0], 400)
        self.assertTrue(path.is_file())

    def test_running_experiment_cannot_be_changed_but_an_independent_copy_is_allowed(self):
        name, record = self.source()
        with patch.object(self.repo.runtime, "snapshot", return_value={"controller_alive": True, "state": "running", "sequence_id": name}):
            self.assertEqual(self.request("POST", "/api/experiments", {"sequence": self.sequence, "expected_version": record["version"]})[0], 409)
            self.assertEqual(self.request("POST", "/api/experiments/" + name + "/rename", {"new_id": "renamed", "expected_version": record["version"]})[0], 409)
            self.assertEqual(self.request("DELETE", "/api/experiments/" + name, {"expected_version": record["version"]})[0], 409)
            self.assertEqual(self.request("POST", "/api/experiments/" + name + "/duplicate", {"new_id": "copy_while_running", "expected_version": record["version"]})[0], 200)

    def test_failed_rename_rolls_back_written_files_and_retains_the_backup(self):
        name, record = self.source()
        path = self.lab / "config/sequences" / (name + ".json")
        original, notes = path.read_bytes(), path.with_suffix(".md").read_bytes()
        write = self.repo._atomic_write
        failed = False
        def fail_once(destination, data):
            nonlocal failed
            if destination.name == "failed_rename.md" and not failed:
                failed = True
                raise OSError("Synthetic write failure")
            return write(destination, data)
        with patch.object(self.repo, "_atomic_write", side_effect=fail_once):
            self.assertEqual(self.request("POST", "/api/experiments/" + name + "/rename", {"new_id": "failed_rename", "expected_version": record["version"]})[0], 500)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(path.with_suffix(".md").read_bytes(), notes)
        self.assertFalse(path.with_name("failed_rename.json").exists())
        self.assertTrue(list((self.root / "data/backups").glob("*/" + path.name)))

    def test_failed_description_save_rolls_back_the_json_and_failed_delete_restores_the_pair(self):
        name, record = self.source()
        path = self.lab / "config/sequences" / (name + ".json")
        original, notes = path.read_bytes(), path.with_suffix(".md").read_bytes()
        write = self.repo._atomic_write
        failed = False
        def fail_once(destination, data):
            nonlocal failed
            if destination.resolve() == path.with_suffix(".md").resolve() and not failed:
                failed = True
                raise OSError("Synthetic write failure")
            return write(destination, data)
        updated = {**self.sequence, "description": "A changed description"}
        with patch.object(self.repo, "_atomic_write", side_effect=fail_once):
            self.assertEqual(self.request("POST", "/api/experiments", {"sequence": updated, "expected_version": record["version"]})[0], 500)
        self.assertEqual(path.read_bytes(), original)
        unlink = Path.unlink
        failed = False
        def fail_unlink(destination, *args, **kwargs):
            nonlocal failed
            if destination.resolve() == path.with_suffix(".md").resolve() and not failed:
                failed = True
                raise OSError("Synthetic unlink failure")
            return unlink(destination, *args, **kwargs)
        with patch.object(Path, "unlink", fail_unlink):
            self.assertEqual(self.request("DELETE", "/api/experiments/" + name, {"expected_version": record["version"]})[0], 500)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(path.with_suffix(".md").read_bytes(), notes)

    def test_files_with_reserved_word_prefixes_are_still_data_files(self):
        name, record = self.source()
        code, result = self.request("POST", "/api/experiments/" + name + "/duplicate", {"new_id": "stopwatch_demo", "expected_version": record["version"]})
        self.assertEqual(code, 200)
        self.assertEqual(self.request("POST", "/api/experiments/stopwatch_demo/rename", {"new_id": "probe_demo", "expected_version": result["version"]})[0], 200)
        self.assertEqual(self.request("POST", "/api/execute", {})[0], 403)

    def test_a_stale_editor_cannot_silently_recreate_a_deleted_file(self):
        name, record = self.source()
        self.assertEqual(self.request("DELETE", "/api/experiments/" + name, {"expected_version": record["version"]})[0], 200)
        code, result = self.request("POST", "/api/experiments", {"sequence": self.sequence, "expected_version": record["version"]})
        self.assertEqual((code, result["error"]), (409, "version_conflict"))
        self.assertFalse((self.lab / "config/sequences" / (name + ".json")).exists())
        self.assertEqual(self.request("POST", "/api/experiments", {"sequence": self.sequence})[0], 200)

    def test_backup_metadata_never_overwrites_a_plan_named_operation(self):
        sequence = {**self.sequence, "id": "operation"}
        _, record = self.request("POST", "/api/experiments", {"sequence": sequence})
        code, result = self.request("DELETE", "/api/experiments/operation", {"expected_version": record["version"]})
        self.assertEqual(code, 200)
        backup = self.root / "data/backups" / result["backup_id"]
        self.assertEqual(json.loads((backup / "operation.json").read_text()), sequence)
        self.assertEqual(json.loads((backup / "operation.meta.json").read_text())["action"], "delete")


if __name__ == "__main__":
    unittest.main()
