from __future__ import annotations

import io
import sys
import threading
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TextIO


@dataclass
class _TeeStream(io.TextIOBase):
    original: TextIO
    file_handle: TextIO
    label: str
    lock: threading.Lock
    _buffer: str = ""

    def write(self, text: str) -> int:
        if not text:
            return 0

        self.original.write(text)
        with self.lock:
            self._buffer += text
            self._drain_complete_lines()
        return len(text)

    def flush(self) -> None:
        self.original.flush()
        with self.lock:
            if not self.file_handle.closed:
                self.file_handle.flush()

    def finalize(self) -> None:
        with self.lock:
            if self._buffer and not self.file_handle.closed:
                self.file_handle.write(f"{self._stamp()} [{self.label}] {self._buffer.rstrip()}\n")
                self._buffer = ""
            if not self.file_handle.closed:
                self.file_handle.flush()

    def isatty(self) -> bool:
        return bool(getattr(self.original, "isatty", lambda: False)())

    @property
    def encoding(self) -> str:
        return str(getattr(self.original, "encoding", "utf-8"))

    @property
    def errors(self) -> str | None:
        return getattr(self.original, "errors", None)

    def _drain_complete_lines(self) -> None:
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self.file_handle.write(f"{self._stamp()} [{self.label}] {line}\n")
            self.file_handle.flush()

    @staticmethod
    def _stamp() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class BotLogCapture(AbstractContextManager["BotLogCapture"]):
    def __init__(self, log_path: str | Path) -> None:
        self.log_path = Path(log_path)
        self._file_handle: TextIO | None = None
        self._orig_stdout: TextIO | None = None
        self._orig_stderr: TextIO | None = None
        self._stdout_tee: _TeeStream | None = None
        self._stderr_tee: _TeeStream | None = None
        self._lock = threading.Lock()

    def __enter__(self) -> "BotLogCapture":
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._file_handle = self.log_path.open("a", encoding="utf-8", buffering=1)
        self._orig_stdout = sys.stdout
        self._orig_stderr = sys.stderr
        self._stdout_tee = _TeeStream(self._orig_stdout, self._file_handle, "stdout", self._lock)
        self._stderr_tee = _TeeStream(self._orig_stderr, self._file_handle, "stderr", self._lock)
        sys.stdout = self._stdout_tee
        sys.stderr = self._stderr_tee
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self._stdout_tee is not None:
                self._stdout_tee.finalize()
            if self._stderr_tee is not None:
                self._stderr_tee.finalize()
        finally:
            if self._orig_stdout is not None:
                sys.stdout = self._orig_stdout
            if self._orig_stderr is not None:
                sys.stderr = self._orig_stderr
            if self._file_handle is not None:
                self._file_handle.close()
