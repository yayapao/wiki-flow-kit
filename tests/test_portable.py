import importlib.util
import io
import json
import os
from pathlib import Path
import plistlib
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
import zipfile

MODULE_PATH = Path(__file__).parents[1] / "packaging" / "portable.py"
SPEC = importlib.util.spec_from_file_location("wiki_flow_portable", MODULE_PATH)
portable = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(portable)
BUILD_SPEC = importlib.util.spec_from_file_location("wiki_flow_build_release", MODULE_PATH.with_name("build_release.py"))
build = importlib.util.module_from_spec(BUILD_SPEC)
BUILD_SPEC.loader.exec_module(build)


class PortableTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.workspace = self.home / "Documents" / "wiki-flow"
        home_patch = patch.object(portable, "app_home", return_value=self.home)
        home_patch.start()
        self.addCleanup(home_patch.stop)

    def tearDown(self):
        self.temp.cleanup()

    def paths(self):
        with patch.object(portable, "app_home", return_value=self.home):
            return portable.workspace_paths(self.workspace)

    def test_scaffold_and_profile_use_current_home_and_relative_roots(self):
        paths = self.paths()
        with patch.object(portable, "architecture", return_value="x86_64"):
            portable.make_scaffold(paths)
            config = portable.write_profile(paths, "/usr/bin/python3", "/usr/local/bin/node", self.home / "package")
        self.assertTrue((paths["valut"] / "purpose.md").is_file())
        self.assertTrue((paths["valut"] / ".llm-wiki/project.json").is_file())
        self.assertEqual(config["platform"], "x86_64")
        self.assertEqual(config["general"]["path"], str(paths["valut"]))
        self.assertNotIn("/Users/yango", paths["registry"].read_text())
        self.assertIn(str(paths["aiwiki"]), paths["qmd_config"].read_text())

    def test_bundle_round_trip_verifies_hashes_and_excludes_machine_state(self):
        paths = self.paths()
        with patch.object(portable, "architecture", return_value="arm64"):
            portable.make_scaffold(paths)
            (paths["aiwiki"] / "amg").mkdir()
            (paths["aiwiki"] / "amg" / "README.md").write_text("AMG docs")
            old_root = str(paths["aiwiki"])
            (paths["aiwiki"] / "AGENTS.md").write_text("Read " + old_root + "/amg/README.md")
            (paths["valut"] / "raw/sources/original.md").write_text("Historical source: " + old_root)
            (paths["valut"] / "wiki" / "notes.md").write_text("general notes")
            (paths["valut"] / ".llm-wiki" / "conversations.json").write_text("private conversation")
            bundle = self.home / "migration.wiki-flow.zip"
            with patch.object(portable, "workspace_paths", return_value=paths):
                portable.create_bundle(type("Args", (), {"workspace": str(self.workspace), "output": str(bundle)})())

            target_home = self.home / "target-home"
            target_workspace = target_home / "Documents" / "wiki-flow"
            with patch.object(portable, "app_home", return_value=target_home), patch.object(portable, "architecture", return_value="x86_64"), patch.object(portable, "resolve_runtime", side_effect=lambda name, _formula, _version: "/usr/bin/" + name):
                result = portable.restore_bundle(type("Args", (), {"workspace": str(target_workspace), "bundle": str(bundle)})())
            self.assertEqual(result, 0)
            self.assertEqual((target_workspace / "aiwiki/amg/README.md").read_text(), "AMG docs")
            self.assertEqual((target_workspace / "valut/wiki/notes.md").read_text(), "general notes")
            self.assertFalse((target_workspace / "valut/.llm-wiki/conversations.json").exists())
            self.assertTrue((target_workspace / "valut/.llm-wiki/project.json").is_file())
            self.assertIn(str(target_workspace / "aiwiki"), (target_workspace / "aiwiki/AGENTS.md").read_text())
            self.assertEqual((target_workspace / "valut/raw/sources/original.md").read_text(), "Historical source: " + old_root)

    def test_restore_rejects_hash_tampering_without_extracting(self):
        bundle = self.home / "tampered.zip"
        with zipfile.ZipFile(bundle, "w") as archive:
            archive.writestr("manifest.json", json.dumps({"schemaVersion": 1, "files": [
                {"path": "aiwiki/README.md", "sha256": "0" * 64},
                {"path": "valut/wiki/index.md", "sha256": portable.hashlib.sha256(b"# index").hexdigest()},
            ]}))
            archive.writestr("aiwiki/README.md", "tampered")
            archive.writestr("valut/wiki/index.md", "# index")
        paths = self.paths()
        with patch.object(portable, "workspace_paths", return_value=paths):
            with self.assertRaisesRegex(RuntimeError, "SHA-256 mismatch"):
                portable.restore_bundle(type("Args", (), {"workspace": str(self.workspace), "bundle": str(bundle)})())
        self.assertEqual(list(paths["root"].iterdir()), [])

    def test_restore_rejects_path_traversal(self):
        bundle = self.home / "unsafe.zip"
        with zipfile.ZipFile(bundle, "w") as archive:
            archive.writestr("../outside.txt", "bad")
        paths = self.paths()
        paths["root"].mkdir(parents=True)
        with patch.object(portable, "workspace_paths", return_value=paths):
            with self.assertRaisesRegex(RuntimeError, "Unsafe bundle path"):
                portable.restore_bundle(type("Args", (), {"workspace": str(self.workspace), "bundle": str(bundle)})())
        self.assertFalse((self.home / "outside.txt").exists())

    def test_sync_plan_detects_conflict_and_reports_removed_source_without_deleting(self):
        paths = self.paths()
        portable.make_scaffold(paths)
        source = paths["aiwiki"] / "amg" / "README.md"
        source.parent.mkdir(parents=True)
        source.write_text("current source")
        target = paths["valut"] / "raw/sources/aiwiki/amg/README.md"
        target.parent.mkdir(parents=True)
        target.write_text("manual edit")
        state = paths["valut"] / ".llm-wiki/wiki-flow-sync.json"
        state.write_text(json.dumps({"files": {"amg/README.md": "old-hash", "gone.md": "gone-hash"}}))
        with patch.object(portable, "workspace_paths", return_value=paths):
            _target, _state_path, _current, plan, stale, _old_state = portable.sync_plan(paths)
        self.assertEqual(plan[0]["status"], "conflict")
        self.assertEqual(stale, ["gone.md"])
        self.assertEqual(target.read_text(), "manual edit")

    def test_intel_pdfium_checksum_is_verified_from_upstream_repository_root(self):
        source = self.home / "llm-wiki"
        pdfium = source / "src-tauri" / "pdfium"
        pdfium.mkdir(parents=True)
        library = pdfium / "libpdfium-x86_64.dylib"
        library.write_bytes(b"intel pdfium")
        checksum = portable.hashlib.sha256(library.read_bytes()).hexdigest()
        (pdfium / "SHA256SUMS").write_bytes((checksum + "  src-tauri/pdfium/libpdfium-x86_64.dylib\r\n").encode())

        portable.prepare_intel_pdfium(source)
        self.assertEqual((pdfium / "libpdfium.dylib").read_bytes(), b"intel pdfium")

    def test_intel_pdfium_checksum_rejects_tampering_before_copy(self):
        source = self.home / "llm-wiki"
        pdfium = source / "src-tauri" / "pdfium"
        pdfium.mkdir(parents=True)
        (pdfium / "libpdfium-x86_64.dylib").write_bytes(b"tampered")
        (pdfium / "SHA256SUMS").write_text("0" * 64 + "  src-tauri/pdfium/libpdfium-x86_64.dylib\n")
        with self.assertRaisesRegex(RuntimeError, "checksum verification failed"):
            portable.prepare_intel_pdfium(source)
        self.assertFalse((pdfium / "libpdfium.dylib").exists())

    def test_release_dmg_asset_selects_native_architecture(self):
        release = {"assets": [
            {"name": "LLM_Wiki_aarch64.dmg"},
            {"name": "LLM_Wiki_x86_64.dmg"},
            {"name": "source.zip"},
        ]}
        self.assertEqual(portable.release_dmg_asset(release, "arm64")["name"], "LLM_Wiki_aarch64.dmg")
        self.assertEqual(portable.release_dmg_asset(release, "x86_64")["name"], "LLM_Wiki_x86_64.dmg")
        self.assertIsNone(portable.release_dmg_asset({"assets": []}, "x86_64"))

    def test_update_apply_requires_release_sha256_before_download(self):
        release = {
            "tag_name": "v0.2.0",
            "assets": [{"name": "wiki-flow-kit-v0.2.0.zip", "browser_download_url": "https://example.invalid/package.zip"}],
        }
        args = type("Args", (), {"repository": "example/wiki-flow-kit", "apply": True, "workspace": None})()
        with patch.object(portable, "github_json", return_value=release), patch.object(portable, "download_asset") as download:
            with self.assertRaisesRegex(RuntimeError, "missing a verifiable SHA-256 digest"):
                portable.update_command(args)
        download.assert_not_called()

    def test_management_cli_init_creates_workspace_and_profile(self):
        with patch.object(portable, "app_home", return_value=self.home), \
                patch.object(portable, "architecture", return_value="arm64"), \
                patch.object(portable, "resolve_runtime", side_effect=lambda name, _formula, _version: "/test/bin/" + name), \
                redirect_stdout(io.StringIO()):
            result = portable.main(["init", "--workspace", str(self.workspace)])
        self.assertEqual(result, 0)
        self.assertTrue((self.workspace / "aiwiki").is_dir())
        profile = json.loads((self.home / ".config/wiki-flow/registry.json").read_text())
        self.assertEqual(profile["platform"], "arm64")

    def test_package_replacement_rolls_back_when_new_directory_cannot_be_installed(self):
        source = self.home / "new-package"
        (source / "bin").mkdir(parents=True)
        (source / "VERSION").write_text("0.2.0")
        (source / "bin/wiki-flow").write_text("new")
        destination = self.home / "installed"
        (destination / "bin").mkdir(parents=True)
        (destination / "VERSION").write_text("0.1.0")
        (destination / "bin/wiki-flow").write_text("old")
        original_replace = portable.os.replace
        calls = 0

        def fail_install_once(src, dst):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated install failure")
            return original_replace(src, dst)

        with patch.object(portable.os, "replace", side_effect=fail_install_once):
            with self.assertRaisesRegex(OSError, "simulated install failure"):
                portable.copy_package(source, destination)
        self.assertEqual((destination / "VERSION").read_text(), "0.1.0")
        self.assertEqual((destination / "bin/wiki-flow").read_text(), "old")
        self.assertEqual([item.name for item in self.home.iterdir() if ".installing-" in item.name], [])

    def test_package_update_preserves_custom_machine_profile(self):
        paths = self.paths()
        paths["data"].mkdir(parents=True)
        app = paths["data"] / "app"
        (app / "bin").mkdir(parents=True)
        (app / "skills/wiki-flow").mkdir(parents=True)
        (app / "packaging").mkdir(parents=True)
        (app / "VERSION").write_text("0.1.0")
        (app / "bin/wiki-flow").write_text("old")
        (app / "packaging/portable.py").write_text("old manager")
        (app / "skills/wiki-flow/SKILL.md").write_text("old skill")
        paths["registry"].parent.mkdir(parents=True)
        paths["registry"].write_text(json.dumps({"custom": "keep", "install": {"version": "0.1.0"}}))
        (paths["data"] / "install-receipt.json").write_text(json.dumps({"architecture": "arm64"}))

        source = self.home / "release/wiki-flow"
        (source / "bin").mkdir(parents=True)
        (source / "skills/wiki-flow").mkdir(parents=True)
        (source / "packaging").mkdir(parents=True)
        (source / "VERSION").write_text("0.2.0")
        (source / "README.md").write_text("new package")
        (source / "bin/wiki-flow").write_text("new")
        (source / "packaging/portable.py").write_text("new manager")
        (source / "skills/wiki-flow/SKILL.md").write_text("new skill")

        with patch.object(portable, "resolve_runtime", return_value="/test/python3"):
            portable.apply_package_update(paths, source)

        profile = json.loads(paths["registry"].read_text())
        receipt = json.loads((paths["data"] / "install-receipt.json").read_text())
        self.assertEqual(profile["custom"], "keep")
        self.assertEqual(profile["install"]["version"], "0.2.0")
        self.assertEqual(receipt["architecture"], "arm64")
        self.assertEqual(receipt["version"], "0.2.0")
        self.assertEqual((paths["skill"] / "SKILL.md").read_text(), "new skill")

    def test_default_management_paths_honor_registered_workspace(self):
        paths = self.paths()
        paths["registry"].parent.mkdir(parents=True)
        registered = self.home / "custom-workspace"
        paths["registry"].write_text(json.dumps({
            "aiwikiRoot": str(registered / "aiwiki"),
            "general": {"path": str(registered / "valut")},
        }))
        selected = portable.workspace_paths()
        self.assertEqual(selected["root"], registered)
        self.assertEqual(selected["aiwiki"], registered / "aiwiki")
        self.assertEqual(portable.workspace_paths(self.workspace)["root"], self.workspace)

    def test_documented_check_and_dry_run_flags_are_read_only(self):
        paths = self.paths()
        portable.make_scaffold(paths)
        source = paths["aiwiki"] / "README.md"
        source.write_text("project docs")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(portable.main(["sync", "--dry-run", "--workspace", str(self.workspace)]), 0)
            with patch.object(portable, "github_json", return_value={"tag_name": "v0.1.0"}):
                self.assertEqual(portable.main(["update", "--check", "--workspace", str(self.workspace)]), 0)
        self.assertFalse((paths["valut"] / "raw/sources/aiwiki").exists())
        self.assertFalse((paths["config"] / "sync.lock").exists())
        self.assertFalse(paths["registry"].exists())

    def test_sync_rejects_destination_symlink_without_touching_external_files(self):
        paths = self.paths()
        portable.make_scaffold(paths)
        source = paths["aiwiki"] / "amg/README.md"
        source.parent.mkdir()
        source.write_text("source")
        outside = self.home / "outside"
        outside.mkdir()
        (outside / "README.md").write_text("keep external file")
        target = paths["valut"] / "raw/sources/aiwiki"
        target.mkdir()
        (target / "amg").symlink_to(outside, target_is_directory=True)
        with redirect_stdout(io.StringIO()):
            result = portable.sync_command(type("Args", (), {"workspace": str(self.workspace), "apply": True})())
        self.assertEqual(result, 1)
        self.assertEqual((outside / "README.md").read_text(), "keep external file")

    def test_sync_retries_rescan_then_defers_refresh_until_both_app_queues_are_idle(self):
        paths = self.paths()
        portable.make_scaffold(paths)
        (paths["aiwiki"] / "README.md").write_text("project docs")
        with patch.object(portable, "architecture", return_value="arm64"):
            portable.write_profile(paths, "/test/python", "/test/node")
        cli = paths["data"] / "app/bin/wiki-flow"
        cli.parent.mkdir(parents=True)
        cli.write_text("placeholder")
        args = type("Args", (), {"workspace": str(self.workspace), "apply": True})()
        state = paths["valut"] / ".llm-wiki/wiki-flow-sync.json"
        queue = paths["valut"] / ".llm-wiki/file-change-queue.json"
        with patch.object(portable, "api_post", return_value={"available": False}) as api, \
                patch.object(portable, "run", return_value="refreshed") as refresh, redirect_stdout(io.StringIO()):
            self.assertEqual(portable.sync_command(args), 0)
            refresh.assert_not_called()
            self.assertTrue(json.loads(state.read_text())["rescanPending"])
            api.return_value = {"available": True}
            queue.write_text(json.dumps({"tasks": [{"status": "processing"}]}))
            self.assertEqual(portable.sync_command(args), 0)
            self.assertEqual(api.call_count, 2)
            refresh.assert_not_called()
            self.assertFalse(json.loads(state.read_text())["rescanPending"])
            queue.write_text(json.dumps({"tasks": []}))
            self.assertEqual(portable.sync_command(args), 0)
            refresh.assert_called_once()
            self.assertFalse(json.loads(state.read_text())["refreshPending"])

    def test_launch_agent_uses_installed_manager_and_explicit_registry(self):
        paths = self.paths()
        paths["data"].mkdir(parents=True)
        paths["registry"].parent.mkdir(parents=True)
        paths["registry"].write_text(json.dumps({
            "workspaceRoot": str(paths["root"]),
            "aiwikiRoot": str(paths["aiwiki"]),
            "general": {"path": str(paths["valut"])},
        }))
        manager = paths["data"] / "portable.py"
        manager.write_text("placeholder")
        with patch.object(portable, "resolve_runtime", return_value="/test/python3"), \
                patch.object(portable.subprocess, "run"), patch.object(portable, "run"), redirect_stdout(io.StringIO()):
            self.assertEqual(portable.create_launch_agent(paths), 0)
        plist = self.home / "Library/LaunchAgents/com.wiki-flow.sync.plist"
        value = plistlib.loads(plist.read_bytes())
        self.assertEqual(value["ProgramArguments"], ["/test/python3", str(manager), "sync", "--apply"])
        self.assertEqual(value["EnvironmentVariables"]["WIKI_FLOW_CONFIG"], str(paths["registry"]))
        self.assertFalse(value["RunAtLoad"])
        with patch.dict(os.environ, {"WIKI_FLOW_CONFIG": str(paths["registry"])}):
            selected = portable.workspace_paths()
        self.assertEqual(selected["aiwiki"], paths["aiwiki"])
        self.assertEqual(selected["valut"], paths["valut"])

    def test_update_checks_do_not_downgrade_newer_local_version(self):
        args = type("Args", (), {"repository": "example/wiki-flow-kit", "apply": True, "workspace": str(self.workspace)})()
        with patch.object(portable, "github_json", return_value={"tag_name": "v0.0.9"}), \
                patch.object(portable, "download_asset") as download, redirect_stdout(io.StringIO()):
            self.assertEqual(portable.update_command(args), 0)
        download.assert_not_called()

    def test_release_package_download_and_update_preserve_knowledge_and_machine_config(self):
        paths = self.paths()
        portable.make_scaffold(paths)
        note = paths["valut"] / "wiki/personal.md"
        note.write_text("keep private knowledge")
        paths["registry"].parent.mkdir(parents=True)
        paths["registry"].write_text(json.dumps({"custom": "keep", "updates": {"repository": "example/wiki-flow-kit"}}))
        paths["qmd_config"].parent.mkdir(parents=True)
        paths["qmd_config"].write_text("keep qmd settings")
        (self.home / ".workbuddy").mkdir()
        source = self.home / "release-source"
        portable.copy_package(MODULE_PATH.parents[1], source)
        (source / "VERSION").write_text("0.2.0")
        archive = build.build_release(source, self.home / "release-dist")
        contents = archive.read_bytes()
        release = {"tag_name": "v0.2.0", "assets": [{
            "name": archive.name,
            "browser_download_url": "https://example.invalid/release.zip",
            "digest": "sha256:" + portable.hashlib.sha256(contents).hexdigest(),
        }]}
        args = type("Args", (), {"repository": None, "apply": True, "workspace": str(self.workspace)})()
        with patch.object(portable, "github_json", return_value=release), \
                patch.object(portable.urllib.request, "urlopen", side_effect=lambda *_args, **_kwargs: io.BytesIO(contents)), \
                patch.object(portable, "resolve_runtime", return_value="/test/python3"), redirect_stdout(io.StringIO()):
            self.assertEqual(portable.update_command(args), 0)
        self.assertEqual((paths["data"] / "app/VERSION").read_text(), "0.2.0")
        self.assertEqual(note.read_text(), "keep private knowledge")
        self.assertEqual(json.loads(paths["registry"].read_text())["custom"], "keep")
        self.assertEqual(paths["qmd_config"].read_text(), "keep qmd settings")
        self.assertTrue((self.home / ".workbuddy/skills/wiki-flow/SKILL.md").is_file())
        self.assertFalse((self.home / ".workbuddy/skills/wiki-flow").is_symlink())
        with zipfile.ZipFile(archive) as package:
            self.assertFalse(any("__pycache__" in name or name.startswith(("aiwiki/", "valut/")) for name in package.namelist()))

    def test_release_download_rejects_tampered_bytes(self):
        destination = self.home / "release.zip"
        with patch.object(portable.urllib.request, "urlopen", return_value=io.BytesIO(b"tampered")):
            with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                portable.download_asset("https://example.invalid/release.zip", destination, "sha256:" + "0" * 64)
        self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
