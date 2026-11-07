"""Stockfish UCI wrapper."""
import os
import queue
import re
import subprocess
import threading
import time


class Engine:
    STARTUP_TIMEOUT = 10.0
    ANALYSIS_GRACE = 3.0

    def __init__(self, path, threads=2, hash_mb=128):
        if not os.path.isfile(path):
            raise FileNotFoundError(
                f"Stockfish not found at {path}. Run: python setup.py")
        self.path = path
        self.threads = threads
        self.hash_mb = hash_mb
        self.proc = None
        self._lines = None
        self._reader = None
        self._start()

    def _start(self):
        self.proc = subprocess.Popen(
            [self.path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self._lines = queue.Queue()
        output = self.proc.stdout
        lines = self._lines

        def read_output():
            try:
                for line in iter(output.readline, ""):
                    lines.put(line)
            finally:
                lines.put(None)

        self._reader = threading.Thread(target=read_output, daemon=True)
        self._reader.start()
        try:
            self._send("uci")
            self._wait("uciok", self.STARTUP_TIMEOUT)
            self._send(f"setoption name Threads value {self.threads}")
            self._send(f"setoption name Hash value {self.hash_mb}")
            self._send("isready")
            self._wait("readyok", self.STARTUP_TIMEOUT)
        except (BrokenPipeError, OSError, TimeoutError, ValueError):
            self.quit()
            raise

    def _restart(self):
        self.quit()
        self._start()

    def _send(self, cmd):
        if self.proc is None or self.proc.poll() is not None:
            raise BrokenPipeError("Stockfish is not running")
        self.proc.stdin.write(cmd + "\n")
        self.proc.stdin.flush()

    def _readline(self, timeout):
        try:
            line = self._lines.get(timeout=timeout)
        except queue.Empty as error:
            raise TimeoutError("Stockfish stopped responding") from error
        if line is None:
            raise BrokenPipeError("Stockfish closed its output")
        return line

    def _wait(self, token, timeout):
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Stockfish did not send {token}")
            line = self._readline(remaining)
            if token in line:
                return

    def analyze(self, fen, movetime_ms=500):
        """Return (uci_move, cp_score, mate_in). cp and mate are mutually exclusive."""
        try:
            return self._analyze(fen, movetime_ms)
        except (BrokenPipeError, OSError, TimeoutError, ValueError):
            # Windows can report Errno/WinError 22 when a child pipe has gone
            # away. Recreate Stockfish and retry this position once so later
            # suggestions continue without restarting ChessHelper.
            self._restart()
            return self._analyze(fen, movetime_ms)

    def _analyze(self, fen, movetime_ms):
        self._send("position fen " + fen)
        self._send(f"go movetime {movetime_ms}")
        best = None
        cp = None
        mate = None
        response_timeout = max(
            self.STARTUP_TIMEOUT,
            movetime_ms / 1000.0 + self.ANALYSIS_GRACE,
        )
        deadline = time.monotonic() + response_timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Stockfish analysis timed out")
            line = self._readline(remaining)
            line = line.strip()
            if line.startswith("bestmove"):
                parts = line.split()
                if len(parts) >= 2 and parts[1] != "(none)":
                    best = parts[1]
                break
            if line.startswith("info") and " score " in line:
                m = re.search(r"score (cp|mate) (-?\d+)", line)
                if m:
                    if m.group(1) == "cp":
                        cp = int(m.group(2)) / 100.0
                        mate = None
                    else:
                        mate = int(m.group(2))
                        cp = None
        return best, cp, mate

    def quit(self):
        if self.proc is None:
            return
        try:
            if self.proc.poll() is None:
                self._send("quit")
                self.proc.wait(timeout=2)
        except (BrokenPipeError, OSError, subprocess.TimeoutExpired, ValueError):
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait(timeout=2)
        finally:
            self.proc = None
