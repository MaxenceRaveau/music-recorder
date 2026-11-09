"""
Runs conversion jobs in parallel for the GUI page. Each worker registers its
live ffmpeg process so `stop()` can kill them all at once; the lock is never
held while calling `on_result`, which may itself call `stop()`.
"""
from __future__ import annotations

import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from .ffmpeg import JobCancelled, JobFailed, process_job
from .scan import Action, Job


class Runner:
    def __init__(self, jobs: list[Job], target: str, *, workers: int | None = None,
                 on_result: Callable[[Job, str, str], None]) -> None:
        self._jobs = jobs
        self._target = target
        self._workers = workers or max(1, (os.cpu_count() or 2) - 1)
        self._on_result = on_result
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._procs: set[subprocess.Popen] = set()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            procs = list(self._procs)
        for p in procs:
            try:
                p.kill()
            except OSError:
                pass

    def run(self) -> bool:
        with ThreadPoolExecutor(max_workers=self._workers) as pool:
            for f in [pool.submit(self._one, j) for j in self._jobs]:
                f.result()
        return not self._stop.is_set()

    def _one(self, job: Job) -> None:
        if self._stop.is_set():
            return
        mine: list[subprocess.Popen] = []

        def register(p: subprocess.Popen | None) -> None:
            with self._lock:
                if p is not None:
                    mine.append(p)
                    self._procs.add(p)
                else:
                    self._procs.difference_update(mine)
                    mine.clear()
            if p is not None and self._stop.is_set():
                p.kill()  # stop() raced with this spawn

        try:
            process_job(job, self._target, register=register, cancelled=self._stop.is_set)
        except JobCancelled:
            return
        except JobFailed as e:
            status, msg = "failed", str(e)
        except Exception as e:  # never let one job kill the run
            status, msg = "failed", str(e) or type(e).__name__
        else:
            status, msg = ("copied" if job.action == Action.COPY else "converted"), ""
        self._on_result(job, status, msg)
