"""Browser tests: a real uvicorn on a temporary data directory, driven by Playwright.

No test may reach a host other than the local server: tiles are disabled through
AEREO_MAP_TILE_URL="" and any other request is aborted and fails the test.
"""

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "samples"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, data_dir: Path, env: dict[str, str]):
        self.data_dir = data_dir
        self.env = env
        self.port = _free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        env = {
            **os.environ,
            "AEREO_DATA_DIR": str(self.data_dir),
            "AEREO_MAP_TILE_URL": "",
            **self.env,
        }
        self.proc = subprocess.Popen(  # noqa: S603 (fixed argv, test-only)
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
             "--port", str(self.port), "--log-level", "warning"],
            cwd=ROOT,
            env=env,
        )  # fmt: skip
        for _ in range(150):
            try:
                urllib.request.urlopen(f"{self.base_url}/api/config/", timeout=1)  # noqa: S310
                return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("server did not start")

    def stop(self) -> None:
        if self.proc:
            self.proc.terminate()
            self.proc.wait(10)

    def restart(self) -> None:
        self.stop()
        self.start()


@pytest.fixture
def server_env() -> dict[str, str]:
    return {}


@pytest.fixture
def server(tmp_path, server_env):
    srv = Server(tmp_path / "data", server_env)
    srv.start()
    yield srv
    srv.stop()


@pytest.fixture
def page_ok(page, server):
    """A page that fails the test on any external request or browser error."""
    external, errors = [], []

    def guard(route):
        if route.request.url.startswith(server.base_url):
            route.continue_()
        else:
            external.append(route.request.url)
            route.abort()

    page.route("**/*", guard)
    page.add_init_script("window.__aereoTest = true")
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on(
        "console",
        lambda m: (
            m.type == "error"
            and "Failed to load resource" not in m.text  # expected 4xx/5xx the UI handles
            and errors.append(m.text)
        ),
    )
    page.expected_errors = errors  # tests may remove messages they provoke on purpose
    yield page
    assert external == [], f"external requests attempted: {external}"
    assert errors == [], f"browser errors: {errors}"


def upload_via_ui(page, server, path: Path, crs: str | None = None) -> None:
    """Wait for a completed result or explicit upload outcome before the next action."""
    page.goto(server.base_url + "/")
    page.set_input_files("#file-input", str(path))
    if crs:
        page.fill("#crs-input", crs)
    page.click("#submit-upload")
    # Clicking only starts XHR. Navigating immediately can query history while the
    # record is still PROCESSING, so a FAILED filter legitimately returns no rows.
    page.wait_for_function("""() => location.pathname.startsWith('/files/') ||
        Boolean(document.querySelector('#upload-outcome')?.textContent.trim())""")
