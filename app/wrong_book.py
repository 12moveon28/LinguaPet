"""错题本数据层：本地 JSON 持久化。

- 结构：[{"word","phonetic","meaning","added_at"}, ...]，最新在前
- 按 word（忽略大小写、去空白）去重；重复添加时更新内容并置顶
- 文件不存在自动创建；损坏自动备份重建
- 原子写
供释义弹窗"加入错题本"、列表窗口与全局热键共用一个数据层。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .logger import get_logger
from .paths import WRONG_WORDS_PATH

log = get_logger("wrong_book")

TIME_FMT = "%Y-%m-%d %H:%M:%S"


class WrongBook:
    def __init__(self, path: Path | str = WRONG_WORDS_PATH) -> None:
        self.path = Path(path)
        self._items: list[dict] = []
        self.load()

    # ---------------- 读写 ----------------
    def load(self) -> None:
        if not self.path.exists():
            log.info("错题本不存在，将在首次添加时创建：%s", self.path)
            self._items = []
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, list):
                raise ValueError("错题本根节点不是数组")
            cleaned: list[dict] = []
            seen: set[str] = set()
            for record in data:  # 旧数据容错
                if not isinstance(record, dict):
                    continue
                word = str(record.get("word", "")).strip()
                key = word.lower()
                if not word or key in seen:
                    continue
                seen.add(key)
                cleaned.append(
                    {
                        "word": word,
                        "phonetic": str(record.get("phonetic", "")).strip(),
                        "meaning": str(record.get("meaning", "")).strip(),
                        "added_at": str(record.get("added_at", "")).strip(),
                    }
                )
            self._items = cleaned
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            log.warning("错题本损坏/不可读（%s），备份后重建", exc)
            self._backup_corrupt()
            self._items = []

    def _backup_corrupt(self) -> None:
        try:
            backup = self.path.with_name(
                f"{self.path.name}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}"
            )
            os.replace(self.path, backup)
        except OSError as exc:
            log.error("备份损坏错题本失败：%s", exc)

    def save(self) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f"{self.path.name}.tmp")
            tmp.write_text(
                json.dumps(self._items, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, self.path)
            return True
        except OSError as exc:
            log.error("错题本保存失败：%s", exc)
            return False

    # ---------------- 操作 ----------------
    def all(self) -> list[dict]:
        return [dict(item) for item in self._items]

    def exists(self, word: str) -> bool:
        key = word.strip().lower()
        return any(item["word"].lower() == key for item in self._items)

    def add(self, word: str, phonetic: str = "", meaning: str = "") -> bool:
        """加入错题本。返回是否写入/更新成功。重复添加更新内容并置顶。"""
        word = (word or "").strip()
        if not word:
            return False
        key = word.lower()
        self._items = [item for item in self._items if item["word"].lower() != key]
        self._items.insert(
            0,
            {
                "word": word,
                "phonetic": (phonetic or "").strip(),
                "meaning": (meaning or "").strip(),
                "added_at": time.strftime(TIME_FMT),
            },
        )
        ok = self.save()
        if ok:
            log.info("已加入错题本：%s（共 %s 条）", word, len(self._items))
        return ok

    def update_info(self, word: str, phonetic: str | None = None,
                    meaning: str | None = None) -> bool:
        """补全/更新已存在词条的音标与释义，不改变排序与 added_at。

        仅当新值非空时覆盖（避免查询失败把已有内容清空）。词条不存在返回 False。
        """
        key = word.strip().lower()
        changed = False
        for item in self._items:
            if item["word"].lower() == key:
                if phonetic and phonetic.strip():
                    item["phonetic"] = phonetic.strip()
                    changed = True
                if meaning and meaning.strip():
                    item["meaning"] = meaning.strip()
                    changed = True
                break
        if changed:
            return self.save()
        return False

    def remove(self, word: str) -> bool:
        key = word.strip().lower()
        before = len(self._items)
        self._items = [item for item in self._items if item["word"].lower() != key]
        if len(self._items) == before:
            return False
        return self.save()

    def __len__(self) -> int:
        return len(self._items)
