"""config.json 读写：缺省值、损坏备份重建、原子写。"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .logger import get_logger
from .paths import CONFIG_PATH

log = get_logger("config")

DEFAULT_CONFIG: dict[str, Any] = {
    "hotkey": "f2",            # 全局取词热键（pynput 风格：f2 / ctrl+alt+w）
    "add_hotkey": "ctrl+alt+e",  # 全局加入错题本热键
    "tts_rate": 160,
    "tts_volume": 1.0,
    "edge_voice": "en-US-AnaNeural",  # 在线神经女声（少女甜妹）
    "online_tts": True,                # 在线优先；失败自动降级系统离线嗓音
    "pet_pos": [100, 100],
    "autostart": False,
}


class Config:
    """以 dict 为底的轻量配置，访问不到的键回退默认值。"""

    def __init__(self, path: Path | str = CONFIG_PATH) -> None:
        self.path = Path(path)
        self._data: dict[str, Any] = dict(DEFAULT_CONFIG)
        self.load()

    # ---------------- 读取 ----------------
    def load(self) -> None:
        if not self.path.exists():
            log.info("配置文件不存在，使用默认配置：%s", self.path)
            self._data = dict(DEFAULT_CONFIG)
            return
        try:
            # utf-8-sig 同时兼容带/不带 BOM 的文件（记事本常写 BOM）
            raw = self.path.read_text(encoding="utf-8-sig")
            user_data = json.loads(raw)
            if not isinstance(user_data, dict):
                raise ValueError("配置根节点不是 JSON 对象")
            merged = dict(DEFAULT_CONFIG)
            merged.update(user_data)
            self._data = self._normalize(merged)
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            log.warning("配置文件损坏或不可读（%s），备份后重建默认配置", exc)
            self._backup_corrupt()
            self._data = dict(DEFAULT_CONFIG)

    def _normalize(self, data: dict[str, Any]) -> dict[str, Any]:
        """基础类型矫正，避免后续模块拿到异常类型而崩溃。"""
        if not (
            isinstance(data.get("pet_pos"), list)
            and len(data["pet_pos"]) == 2
            and all(isinstance(v, (int, float)) for v in data["pet_pos"])
        ):
            data["pet_pos"] = list(DEFAULT_CONFIG["pet_pos"])
        try:
            data["tts_rate"] = int(data["tts_rate"])
        except (TypeError, ValueError):
            data["tts_rate"] = DEFAULT_CONFIG["tts_rate"]
        try:
            vol = float(data["tts_volume"])
            data["tts_volume"] = min(1.0, max(0.0, vol))
        except (TypeError, ValueError):
            data["tts_volume"] = DEFAULT_CONFIG["tts_volume"]
        data["autostart"] = bool(data.get("autostart", False))
        data["online_tts"] = bool(data.get("online_tts", True))
        if not isinstance(data.get("edge_voice"), str) or not data["edge_voice"].strip():
            data["edge_voice"] = DEFAULT_CONFIG["edge_voice"]
        for key in ("hotkey", "add_hotkey"):
            if not isinstance(data.get(key), str) or not data[key].strip():
                data[key] = DEFAULT_CONFIG[key]
        return data

    def _backup_corrupt(self) -> None:
        try:
            backup = self.path.with_name(
                f"{self.path.name}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}"
            )
            os.replace(self.path, backup)
            log.info("损坏配置已备份为：%s", backup.name)
        except OSError as exc:
            log.error("备份损坏配置失败：%s", exc)

    # ---------------- 访问 ----------------
    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, DEFAULT_CONFIG.get(key, default))

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def update(self, **kwargs: Any) -> None:
        self._data.update(kwargs)

    def as_dict(self) -> dict[str, Any]:
        return dict(self._data)

    # ---------------- 写入 ----------------
    def save(self) -> bool:
        """原子写：先写临时文件再 os.replace。返回是否成功。"""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f"{self.path.name}.tmp")
            tmp.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, self.path)
            return True
        except OSError as exc:
            log.error("配置保存失败：%s", exc)
            return False
