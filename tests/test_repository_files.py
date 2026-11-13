"""Exercise Git's real index and ignore rules without application dependencies."""

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RepositoryFilesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.git("init", "--quiet")
        shutil.copyfile(ROOT / ".gitignore", self.root / ".gitignore")
        (self.root / "tools").mkdir()
        shutil.copyfile(ROOT / "tools/check_repository_files.py",
                        self.root / "tools/check_repository_files.py")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def write(self, path):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("synthetic test content\n")

    def check(self):
        return subprocess.run([sys.executable, "tools/check_repository_files.py"],
                              cwd=self.root, capture_output=True, text=True)

    def test_private_files_do_not_enter_normal_add(self):
        paths = ["docs/reports/my report.json", "docs/research/notes.md",
                 "docs/arbitrary-agent-memory.md",
                 ".local/agent-handoff.md", "docs/IME_DEBUG_HANDOFF_2026-11-12.md",
                 "docs/INPUT_SOURCE_DEACTIVATION_DIAGNOSIS.md",
                 "docs/report-source.md",
                 "data/voice/history.json", "agent-experience/session/events.jsonl",
                 "experiments/new-run/audio.wav", "tools/results/latest.json",
                 "gesture-history.sqlite3-wal", ".llama-server.stdout.log",
                 "tools/data/my_real_history.json", "audio_tests/recording.wav"]
        for path in paths:
            self.write(path)
        self.git("add", ".")
        staged = set(self.git("ls-files", "-z").stdout.decode().split("\0"))
        self.assertFalse(staged.intersection(paths))
        self.assertEqual(self.check().returncode, 0)

    def test_forced_private_add_is_rejected_without_deleting_local_file(self):
        path = "docs/reports/private report.json"
        self.write(path)
        self.git("add", "-f", "--", path)
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn(path, result.stderr)
        self.assertTrue((self.root / path).is_file())

    def test_untracking_private_file_is_allowed(self):
        path = "data/session.json"
        self.write(path)
        self.git("add", "-f", "--", path)
        self.git("rm", "--cached", "--", path)
        self.assertEqual(self.check().returncode, 0)
        self.assertTrue((self.root / path).is_file())

    def test_build_inputs_and_synthetic_fixtures_remain_trackable(self):
        paths = ["src/proximic_ring/assets/firmware/ring-firmware.zip",
                 "src/proximic_ring/assets/ringo-near-v1.model",
                 "src/proximic_ring/ui/qml/Voice.qml",
                 "src/ring_mac_sdk/native/libring.dylib",
                 "native/ProxiMicInput/Sources/InputController.swift",
                 "packaging/mythlink.spec", "scripts/build-macos.sh",
                 "documentation/BUILDING.md", "documentation/BROWSER_EXTENSION.md",
                 "tools/data/automatic_voice_routing_cases.json",
                 "experiments/workflow_trials/cases/music-direct.case.json",
                 "experiments/workflow_recommendations/contexts/music.json",
                 "experiments/ring_desktop_agent/launcher.py"]
        for path in paths:
            self.write(path)
        self.git("add", "--", *paths)
        self.assertEqual(self.check().returncode, 0)

    def test_outputs_next_to_reusable_experiments_stay_private(self):
        paths = ["experiments/workflow_recommendations/dataset/live.json",
                 "experiments/ring_desktop_agent/output.json",
                 "experiments/paraformer_streaming/recording.wav",
                 "experiments/workflow_trials/run-results.json"]
        for path in paths:
            self.write(path)
        self.git("add", ".")
        staged = set(self.git("ls-files", "-z").stdout.decode().split("\0"))
        self.assertFalse(staged.intersection(paths))

    def test_machine_local_excludes_do_not_change_shared_policy(self):
        self.write("src/legitimate.py")
        (self.root / ".git/info/exclude").write_text("src/legitimate.py\n")
        self.git("add", "-f", "src/legitimate.py")
        self.assertEqual(self.check().returncode, 0)


if __name__ == "__main__":
    unittest.main()
