from __future__ import annotations

from pathlib import Path


class WatchlistManager:
    def __init__(self, path: Path, benchmarks: tuple[str, ...] = ("SPY", "QQQ")):
        self.path = path
        self.benchmarks = benchmarks
        self._mtime_ns: int | None = None
        self._symbols: list[str] = []

    @staticmethod
    def normalize(symbols: list[str]) -> list[str]:
        result: list[str] = []
        for raw in symbols:
            symbol = raw.strip().upper()
            if (
                symbol
                and not symbol.startswith("#")
                and symbol.replace(".", "").replace("-", "").isalnum()
                and symbol not in result
            ):
                result.append(symbol)
        return result

    def load(self) -> list[str]:
        supplied = (
            self.normalize(self.path.read_text(encoding="utf-8").splitlines())
            if self.path.exists()
            else []
        )
        self._symbols = self.normalize(supplied + list(self.benchmarks))
        self._mtime_ns = self.path.stat().st_mtime_ns if self.path.exists() else None
        return self._symbols.copy()

    def replace(self, symbols: list[str]) -> list[str]:
        normalized = self.normalize(symbols)
        self.path.write_text("\n".join(normalized) + "\n", encoding="utf-8")
        return self.load()

    def reload_if_changed(self) -> list[str] | None:
        mtime = self.path.stat().st_mtime_ns if self.path.exists() else None
        return self.load() if mtime != self._mtime_ns else None

    @property
    def symbols(self) -> list[str]:
        return self._symbols.copy()
