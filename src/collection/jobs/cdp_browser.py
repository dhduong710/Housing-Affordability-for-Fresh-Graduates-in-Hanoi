"""Own a browser process and temporary profile, then attach Playwright via CDP."""

import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener


def browser_executable(runtime, channel):
    if channel == 'chromium':
        candidate = Path(runtime.chromium.executable_path)
        if candidate.is_file():
            return str(candidate)
        raise FileNotFoundError('Chromium not installed; run: python -m playwright install chromium')
    commands = {'chrome': ['chrome', 'google-chrome', 'google-chrome-stable'],
                'msedge': ['msedge', 'microsoft-edge', 'microsoft-edge-stable']}[channel]
    for command in commands:
        if executable := shutil.which(command):
            return executable
    relative = ('Google/Chrome/Application/chrome.exe' if channel == 'chrome'
                else 'Microsoft/Edge/Application/msedge.exe')
    for name in ['PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA']:
        if base := os.environ.get(name):
            candidate = Path(base) / relative
            if candidate.is_file():
                return str(candidate)
    raise FileNotFoundError(f'{channel} executable not found; install it or set JOBS_CHANNEL=chromium')


class CdpBrowser:
    def __init__(self, runtime, channel, headless, timeout_ms):
        self.runtime, self.channel = runtime, channel
        self.headless, self.timeout_ms = headless, timeout_ms
        self.process = self.browser = self.profile = None

    def start(self):
        executable = browser_executable(self.runtime, self.channel)
        self.profile = tempfile.TemporaryDirectory(prefix='topcv_cdp_', ignore_cleanup_errors=True)
        try:
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                port = listener.getsockname()[1]
            command = [executable, f'--remote-debugging-port={port}',
                       '--remote-debugging-address=127.0.0.1',
                       f'--user-data-dir={self.profile.name}', '--no-first-run',
                       '--no-default-browser-check']
            if self.headless:
                command.append('--headless=new')
            command.append('about:blank')
            self.process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            endpoint = f'http://127.0.0.1:{port}'
            self._wait_ready(endpoint)
            self.browser = self.runtime.chromium.connect_over_cdp(endpoint, timeout=self.timeout_ms)
            return self.browser
        except Exception:
            self.close()
            raise

    def _wait_ready(self, endpoint):
        # Local DevTools traffic must not go through an environment HTTP proxy.
        opener = build_opener(ProxyHandler({}))
        deadline = time.monotonic() + self.timeout_ms / 1000
        while (remaining := deadline - time.monotonic()) > 0:
            if self.process.poll() is not None:
                raise RuntimeError(f'{self.channel} exited before CDP was ready (code {self.process.returncode})')
            try:
                with opener.open(endpoint + '/json/version', timeout=min(1, remaining)) as response:
                    if response.status == 200:
                        return
            except (URLError, OSError):
                pass
            time.sleep(min(0.1, max(0, deadline - time.monotonic())))
        raise TimeoutError('Browser CDP endpoint did not become ready within the configured timeout')

    def close(self):
        try:
            if self.browser:
                try:
                    self.browser.new_browser_cdp_session().send('Browser.close')
                except Exception:  
                    pass  # The owned process is also stopped below if CDP disconnected.
                finally:
                    try:
                        self.browser.close()
                    except Exception:  
                        pass
        finally:
            try:
                if self.process:
                    try:
                        self.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.process.terminate()
                        try:
                            self.process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            self.process.kill()
                            self.process.wait(timeout=5)
            finally:
                if self.profile:
                    self.profile.cleanup()
                self.process = self.browser = self.profile = None
