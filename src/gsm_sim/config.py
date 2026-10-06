"""Nạp và giữ config từ YAML. Dùng dict lồng nhau + truy cập có kiểm tra.

Giữ đơn giản: config là nguồn số duy nhất (không hard-code số trong domain logic).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_MISSING = object()


class Config:
    """Bọc dict config, truy cập bằng dotted key với default rõ ràng."""

    def __init__(self, data: dict[str, Any], root_dir: Path):
        self._data = data
        self.root_dir = root_dir

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        path = Path(path)
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        # root_dir = thư mục chứa repo (giả định config nằm trong configs/ dưới repo root)
        root_dir = path.resolve().parent.parent
        return cls(data, root_dir)

    def get(self, dotted: str, default: Any = _MISSING) -> Any:
        node: Any = self._data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                if default is _MISSING:
                    raise KeyError(f"Config thiếu khóa: {dotted}")
                return default
            node = node[part]
        return node

    def resolve_path(self, dotted: str) -> Path:
        """Đường dẫn tương đối trong config → tuyệt đối theo repo root."""
        rel = self.get(dotted)
        return (self.root_dir / rel).resolve()

    @property
    def data(self) -> dict[str, Any]:
        return self._data
