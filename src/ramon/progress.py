from __future__ import annotations

import sys
import time
from dataclasses import dataclass


def _format_seconds(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


@dataclass
class ProgressReporter:
    """Small stderr progress reporter for long-running Ramon CLI jobs."""

    total: int
    label: str = "progress"
    enabled: bool = True
    min_interval: float = 1.0

    def __post_init__(self) -> None:
        if self.total <= 0:
            self.total = 1
        self.current = 0
        self.started = time.monotonic()
        self.last_render = 0.0
        self.last_stage = ""
        self._finished = False

    def update(self, current: int | None = None, *, advance: int = 0, stage: str = "", force: bool = False) -> None:
        if not self.enabled or self._finished:
            return
        if current is not None:
            self.current = int(current)
        if advance:
            self.current += int(advance)
        self.current = max(0, min(self.current, self.total))
        if stage:
            self.last_stage = stage

        now = time.monotonic()
        if not force and self.current < self.total and now - self.last_render < self.min_interval:
            return
        self.last_render = now

        pct = 100.0 * self.current / self.total
        elapsed = now - self.started
        rate = self.current / elapsed if elapsed > 0 and self.current else 0.0
        eta = (self.total - self.current) / rate if rate > 0 else None
        eta_text = _format_seconds(eta) if eta is not None else "--:--"

        width = 24
        filled = min(width, int(width * self.current / self.total))
        bar = "#" * filled + "-" * (width - filled)
        stage_text = f" | {self.last_stage}" if self.last_stage else ""
        line = (
            f"\r[{bar}] {pct:6.2f}% | {self.current}/{self.total}"
            f" | elapsed {_format_seconds(elapsed)} | ETA {eta_text}{stage_text}"
        )
        print(line, end="", file=sys.stderr, flush=True)

    def step(self, *, stage: str = "", amount: int = 1) -> None:
        self.update(advance=amount, stage=stage)

    def finish(self, *, stage: str = "done") -> None:
        if self._finished:
            return
        self.update(current=self.total, stage=stage, force=True)
        if self.enabled:
            print(file=sys.stderr, flush=True)
        self._finished = True


class ProgressSlice:
    """Map a child operation's [0,total] progress into a parent reporter range."""

    def __init__(self, parent: ProgressReporter, start: int, end: int, total: int, stage: str) -> None:
        self.parent = parent
        self.start = start
        self.end = max(start + 1, end)
        self.total = max(1, total)
        self.stage = stage

    def update(self, current: int, *, stage: str | None = None, force: bool = False) -> None:
        current = max(0, min(int(current), self.total))
        mapped = self.start + int((self.end - self.start) * current / self.total)
        self.parent.update(current=mapped, stage=stage or self.stage, force=force)

    def finish(self, *, stage: str | None = None) -> None:
        self.parent.update(current=self.end, stage=stage or self.stage, force=True)
