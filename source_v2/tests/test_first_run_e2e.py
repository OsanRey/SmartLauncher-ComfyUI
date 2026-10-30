from __future__ import annotations

import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from smart_launcher.application.installation_pipeline import InstallationPipeline
from smart_launcher.application.launcher import LauncherApplication
from smart_launcher.application.server_controller import ServerController
from smart_launcher.comfy.api_client import ComfyApiClient
from smart_launcher.domain.comfy import ServerState
from smart_launcher.domain.environments import EnvironmentHealth, EnvironmentState, RepairResult, RepairStage
from smart_launcher.domain.installation import InstallationRequest
from smart_launcher.domain.runtimes import RuntimeCandidate, RuntimeProbe, RuntimeSource
from smart_launcher.domain.settings import LauncherSettings


_SERVER = r'''
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
parser = argparse.ArgumentParser()
parser.add_argument("--listen")
parser.add_argument("--port", type=int)
args = parser.parse_args()
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/system_stats":
            self.send_error(404); return
        body = json.dumps({"system": {"python_version": "isolated-test"}}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def log_message(self, *_args): pass
ThreadingHTTPServer((args.listen, args.port), Handler).serve_forever()
'''


class _LocalEnvironmentService:
    def rebuild(self, environment, _runtime, _manifests, **_kwargs):
        subprocess.run(
            [sys.executable, "-m", "venv", "--without-pip", str(environment)],
            check=True,
            capture_output=True,
        )
        python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        health = EnvironmentHealth(environment, python, EnvironmentState.HEALTHY, "ok")
        return RepairResult(True, RepairStage.COMMIT, health, health, None, "ok")


class _ApiOnlyClient(ComfyApiClient):
    def frontend(self) -> str:
        return "/assets/index.js"

    def jobs(self, *, limit: int = 200):
        return {"jobs": []}


class FirstRunEndToEndTests(unittest.TestCase):
    def test_independent_install_can_launch_without_legacy_comfy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "repository"
            repository.mkdir()
            self._git(repository, "init", "-b", "master")
            self._git(repository, "config", "user.name", "Smart Launcher Tests")
            self._git(repository, "config", "user.email", "tests@example.invalid")
            (repository / "main.py").write_text(_SERVER, encoding="utf-8")
            (repository / "requirements.txt").write_text("", encoding="utf-8")
            self._git(repository, "add", ".")
            self._git(repository, "commit", "-m", "stable")
            self._git(repository, "tag", "v1.0.0")

            target = root / "IndependentComfy"
            runtime = RuntimeProbe(
                RuntimeCandidate(Path(sys.executable), RuntimeSource.CONFIGURED),
                True,
                Path(sys.executable),
                version_info=sys.version_info[:3],
                release_level="final",
                implementation="cpython",
                architecture="64bit",
                prefix=Path(sys.prefix),
                base_prefix=Path(sys.base_prefix),
                venv_available=True,
                ensurepip_available=True,
            )
            installed = InstallationPipeline(_LocalEnvironmentService()).execute(  # type: ignore[arg-type]
                InstallationRequest(target, str(repository)),
                runtime,
                torch_index_url=None,
            )
            self.assertTrue(installed.succeeded, installed.detail)

            controller = ServerController(api_client_factory=_ApiOnlyClient)
            app = LauncherApplication(
                LauncherSettings(target, target / "venv", manager_enabled=False, port=self._port()),
                controller,
            )
            app.start()
            try:
                self.assertTrue(self._wait(lambda: controller.state is ServerState.READY, 8.0))
                self.assertNotIn("D:\\ComfyUI", str(target))
            finally:
                app.close()
            self.assertTrue(self._wait(lambda: controller.state is ServerState.STOPPED, 4.0))

    @staticmethod
    def _git(cwd: Path, *args: str) -> None:
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

    @staticmethod
    def _port() -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    @staticmethod
    def _wait(predicate, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.025)
        return predicate()


if __name__ == "__main__":
    unittest.main()
