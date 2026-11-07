"""Regression tests for Stockfish process failure recovery."""
import queue
import tempfile
import unittest
from unittest import mock

import chess

import engine as eng


class FakeStdout:
    def __init__(self):
        self.lines = queue.Queue()

    def readline(self):
        return self.lines.get()


class FakeStdin:
    def __init__(self, process):
        self.process = process

    def write(self, text):
        self.process.command(text.strip())

    def flush(self):
        pass


class FakeProcess:
    def __init__(self, answer_analysis, answer_startup=True):
        self.answer_analysis = answer_analysis
        self.answer_startup = answer_startup
        self.alive = True
        self.stdout = FakeStdout()
        self.stdin = FakeStdin(self)

    def command(self, command):
        if command == "uci" and self.answer_startup:
            self.stdout.lines.put("uciok\n")
        elif command == "isready":
            self.stdout.lines.put("readyok\n")
        elif command.startswith("go ") and self.answer_analysis:
            self.stdout.lines.put("info depth 1 score cp 24\n")
            self.stdout.lines.put("bestmove e2e4\n")
        elif command == "quit":
            self.alive = False
            self.stdout.lines.put("")

    def poll(self):
        return None if self.alive else 0

    def wait(self, timeout=None):
        self.alive = False
        return 0

    def terminate(self):
        self.command("quit")

    def kill(self):
        self.command("quit")


class EngineRecoveryTests(unittest.TestCase):
    def test_hung_startup_is_terminated(self):
        process = FakeProcess(False, answer_startup=False)
        with (tempfile.NamedTemporaryFile() as executable,
              mock.patch.object(eng.subprocess, "Popen", return_value=process),
              mock.patch.object(eng.Engine, "STARTUP_TIMEOUT", 0.05),
              self.assertRaises(TimeoutError)):
            eng.Engine(executable.name)
        self.assertFalse(process.alive)

    def test_hung_analysis_restarts_engine_and_retries_once(self):
        processes = [FakeProcess(False), FakeProcess(True)]
        with (tempfile.NamedTemporaryFile() as executable,
              mock.patch.object(eng.subprocess, "Popen", side_effect=processes),
              mock.patch.object(eng.Engine, "STARTUP_TIMEOUT", 0.05),
              mock.patch.object(eng.Engine, "ANALYSIS_GRACE", 0.05)):
            engine = eng.Engine(executable.name)
            try:
                best, score, mate = engine.analyze(chess.STARTING_FEN, 1)
            finally:
                engine.quit()

        self.assertEqual(best, "e2e4")
        self.assertEqual(score, 0.24)
        self.assertIsNone(mate)
        self.assertFalse(processes[0].alive)


if __name__ == "__main__":
    unittest.main()
