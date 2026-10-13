"""Offline regression checks for redacted public-release diagnostics."""

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_public_release.py"
SPEC = importlib.util.spec_from_file_location("release_audit", SCRIPT)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


class ReleaseAuditTests(unittest.TestCase):
    def test_known_secret_location_does_not_expose_value(self):
        value = "random-token-" + "9" * 30
        findings = audit.scan_text("line one\nsecret = " + value, "config.txt", secret_values=[value])
        self.assertEqual(findings, [{"path": "config.txt", "line": 2, "code": "known_secret_value"}])
        self.assertNotIn(value, json.dumps(findings))

    def test_token_pattern_and_placeholder(self):
        token = "sk-" + "r" * 30
        findings = audit.scan_text("API key: " + token, "example.md")
        self.assertEqual(findings[0]["code"], "possible_hardcoded_secret")
        self.assertEqual(audit.scan_text('api_key = "your_api_key_here"', "config.py"), [])

    def test_removed_provider_name_case_insensitive(self):
        term = "".join(("liao", "bot"))
        text = "URL: https://" + term.upper() + "s.example/v1"
        findings = audit.scan_text(text, "config.py", forbidden_terms=[term])
        self.assertEqual(findings[0]["code"], "removed_provider_name")
        self.assertNotIn(text, json.dumps(findings))

    def test_private_absolute_path(self):
        text = "F:" + "\\" + "PaperArena" + "\\old\\report.json"
        self.assertEqual(audit.scan_text(text, "readme.md")[0]["code"], "private_absolute_path")

    def test_notebook_saved_output_and_execution(self):
        document = {"cells": [{"cell_type": "code", "source": ["print('ok')"], "execution_count": 2,
                                "outputs": [{"output_type": "stream", "text": "private output"}]}]}
        findings = audit.scan_notebook(json.dumps(document), "demo.ipynb")
        self.assertEqual({row["code"] for row in findings}, {"notebook_has_outputs", "notebook_executed_cell"})
        self.assertNotIn("private output", json.dumps(findings))

    def test_unexecuted_notebook_is_clean(self):
        text = json.dumps({"cells": [{"cell_type": "code", "source": ["print('ok')"], "execution_count": None, "outputs": []}]})
        self.assertEqual(audit.scan_notebook(text, "demo.ipynb"), [])

    def test_plain_directory_environment_is_blocked(self):
        with tempfile.TemporaryDirectory(dir=SCRIPT.parents[1], prefix=".release-audit-test-") as name:
            root = Path(name)
            self.assertTrue(root.resolve().is_relative_to(SCRIPT.parents[1].resolve()))
            (root / ".env").write_text("API_KEY=", encoding="utf-8")
            (root / ".env.example").write_text("API_KEY=", encoding="utf-8")
            report = audit.audit_repository(root)
            self.assertEqual(report["status"], "BLOCKED")
            self.assertEqual(report["findings"], [{"path": ".env", "line": 1, "code": "private_environment_file"}])

    def test_git_candidates_skip_ignored_but_include_tracked(self):
        with tempfile.TemporaryDirectory(dir=SCRIPT.parents[1], prefix=".release-audit-test-") as name:
            root = Path(name)
            self.assertTrue(root.resolve().is_relative_to(SCRIPT.parents[1].resolve()))
            subprocess.run(["git", "init", "--quiet", str(root)], check=True, capture_output=True)
            (root / ".gitignore").write_text("private.txt\n", encoding="utf-8")
            (root / "private.txt").write_text("sensitive local contents", encoding="utf-8")
            (root / "README.md").write_text("Public example", encoding="utf-8")
            candidates = {path.name for path in audit.candidate_paths(root)}
            self.assertEqual(candidates, {".gitignore", "README.md"})
            self.assertEqual(audit.audit_repository(root)["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
