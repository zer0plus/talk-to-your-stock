from __future__ import annotations

import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryFile

import uvicorn
from fastapi import FastAPI


REPO_ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def running_service(app: FastAPI) -> Iterator[str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, log_level="critical", access_log=False, ws="none")
    )
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [sock]},
        daemon=True,
    )
    thread.start()

    deadline = time.monotonic() + 5
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
        raise RuntimeError("Test service failed to start.")

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()


@contextmanager
def running_service_process(
    app_import: str,
    *,
    environ: Mapping[str, str],
    health_path: str = "/v1/health",
) -> Iterator[str]:
    port = _unused_port()
    base_url = f"http://127.0.0.1:{port}"
    with TemporaryFile(mode="w+") as output:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                app_import,
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
                "--no-access-log",
            ],
            cwd=REPO_ROOT,
            env=dict(environ),
            stdout=output,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            _wait_for_service(
                process=process,
                url=f"{base_url}{health_path}",
                output=output,
            )
            yield base_url
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def _unused_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_service(*, process, url: str, output) -> None:
    import httpx

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        try:
            response = httpx.get(url, timeout=0.5)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.05)

    output.seek(0)
    details = output.read().strip()
    raise RuntimeError(f"Test service failed to start.\n{details}")
