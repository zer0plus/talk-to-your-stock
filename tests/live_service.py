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


class RestartableServiceProcess:
    def __init__(
        self,
        app_import: str,
        *,
        environ: Mapping[str, str],
        health_path: str = "/v1/health",
    ) -> None:
        self._app_import = app_import
        self._environ = dict(environ)
        self._health_path = health_path
        self._port = _unused_port()
        self._output = TemporaryFile(mode="w+")
        self._process: subprocess.Popen[str] | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    def start(self) -> None:
        if self._process is not None:
            raise RuntimeError("Test service is already running.")
        self._output.seek(0)
        self._output.truncate()
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                self._app_import,
                "--host",
                "127.0.0.1",
                "--port",
                str(self._port),
                "--log-level",
                "warning",
                "--no-access-log",
            ],
            cwd=REPO_ROOT,
            env=self._environ,
            stdout=self._output,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self._process = process
        try:
            _wait_for_service(
                process=process,
                url=f"{self.url}{self._health_path}",
                output=self._output,
            )
        except BaseException:
            self.stop()
            raise

    def stop(self) -> None:
        process = self._process
        if process is None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        finally:
            self._process = None

    def restart(self) -> None:
        self.stop()
        self.start()

    def close(self) -> None:
        self.stop()
        self._output.close()


@contextmanager
def restartable_service_process(
    app_import: str,
    *,
    environ: Mapping[str, str],
    health_path: str = "/v1/health",
) -> Iterator[RestartableServiceProcess]:
    service = RestartableServiceProcess(
        app_import,
        environ=environ,
        health_path=health_path,
    )
    service.start()
    try:
        yield service
    finally:
        service.close()


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
