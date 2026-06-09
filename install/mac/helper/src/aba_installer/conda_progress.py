"""Sub-progress for the long `micromamba create` steps.

The install bar is step-count based (done/total steps). Two steps — the conda
Python env (~250 pkgs) and the R/Seurat env — dominate the wall-clock, so the
bar sits frozen for minutes during them ("stays grey, then jumps to blue").

This parser turns a `micromamba create -v` stdout stream into a 0..1 fraction
for the in-flight step, so the bar keeps moving. It keys on per-package
signals (verbose mode is required for the download one):

    Install: N packages                       -> total package count
      + <name> <ver> <build> <channel> <SIZE> -> table row; SIZE 'Cached' = no download
    info ... Download finished ...            -> one per downloaded package
    Linking <name>-<ver>-<build>              -> one per package (link phase)

fraction = (downloaded + linked) / (to_download + total): downloads move the
first portion, links the rest. Cached envs skip downloads, so the bar is just
link progress. Monotonic; 0.0 until the total is known (so non-conda steps,
which never print these lines, simply never report sub-progress)."""
from __future__ import annotations
import re

_INSTALL_N = re.compile(r"Install:\s+(\d+)\s+package")


class CondaProgress:
    def __init__(self) -> None:
        self.total: int | None = None     # packages to install (link target)
        self.to_download = 0              # non-cached table rows
        self.downloaded = 0
        self.linked = 0
        self._table_open = True           # still reading the "+ ..." table?
        self._frac = 0.0

    def feed(self, line: str) -> float:
        """Feed one output line; return the current monotonic fraction (0..1)."""
        s = line.strip()
        m = _INSTALL_N.search(s)
        if m:
            self.total = int(m.group(1))
        elif s.startswith("+ ") and self._table_open:
            # "+ name ver build channel SIZE" — SIZE 'Cached' => already local.
            if not s.endswith("Cached"):
                self.to_download += 1
        elif "Download finished" in s:
            self._table_open = False
            self.downloaded += 1
        elif s.startswith("Linking "):
            self._table_open = False
            self.linked += 1

        if self.total:
            denom = self.to_download + self.total
            if denom:
                f = (self.downloaded + self.linked) / denom
                self._frac = max(self._frac, min(1.0, f))
        return self._frac

    @property
    def fraction(self) -> float:
        return self._frac
