"""词典抽象层与查询服务。

- DictionaryProvider：统一接口
- KdExeProvider：调用 exe 同目录 kd.exe，单次查询超时 2 秒；
  kd.exe 输出格式未知，解析不写死：先按 JSON 启发式提取，失败按纯文本提取。
- BuiltinJsonProvider：data/builtin_dict.json（打包后在 _MEIPASS）
- DictionaryService：kd 优先、内置降级；内存缓存（含负缓存）；
  查询放 QThread，弹窗凭 token 只认本次结果。
- kd.exe 冷启动预热：程序启动后后台线程执行一次 `kd.exe -h`。
"""
from __future__ import annotations

import json
import re
import subprocess
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Signal

from .logger import get_logger
from .paths import BUILTIN_DICT_PATH, KD_EXE_PATH

log = get_logger("dict")

KD_TIMEOUT_SEC = 2.0
WARMUP_TIMEOUT_SEC = 3.0
PHONETIC_KEYS = ("phonetic", "ipa", "pron", "pronunciation", "pronounce", "pinyin", "音标", "读音")
MEANING_KEYS = (
    "meaning", "meanings", "definition", "definitions", "translation",
    "translations", "trans", "gloss", "glossary", "explain", "para",
    "paraphrase", "释义", "解释", "中文", "翻译", "定义",
)
WORD_KEYS = ("word", "term", "headword", "k", "单词")
# urfave/cli 等框架的帮助/用法错误输出，不应被当成释义
_HELP_MARKERS = ("Incorrect Usage", "flag provided but not defined",
                 "GLOBAL OPTIONS:", "NAME:", "USAGE:")
_SLASH_PHONETIC_RE = re.compile(r"/[^\s/\n]{1,60}/")
_BRACKET_PHONETIC_RE = re.compile(r"\[[^\]\n]{1,80}\]")
# 方括号内出现这些 IPA/语种特征才认定为音标，避免误吞 [INFO] 之类
_IPA_HINTS = set("ˈˌ'′æɑɒɐəɚɪʊɛɔœøɵɸβθðʃʒʒŋɲɭɾʁʔʼˌː.ˑˈʌɜɐɵɸʉɨʉ")
_BRACKET_LANG_HINTS = ("美", "英")
# 非释义噪声行特征（守护进程状态、例句标记等）
_NOISE_LINE_HINTS = ("守护进程", "正在启动", "daemon")
_EXAMPLE_LEADERS = ("≫", ">>", "›")


def _looks_like_phonetic(text: str) -> bool:
    body = text.strip("[] ")
    if any(hint in body for hint in _BRACKET_LANG_HINTS):
        return True
    return any(ch in _IPA_HINTS for ch in body)


@dataclass
class DictEntry:
    word: str = ""
    phonetic: str = ""
    meaning: str = ""
    source: str = ""

    def is_empty(self) -> bool:
        return not (self.phonetic or self.meaning)


# ---------------------------------------------------------------- 解析
def _norm_key(key: Any) -> str:
    return str(key).strip().lower().replace("-", "").replace("_", "").replace(" ", "")


def _find_by_keys(obj: dict, candidates: tuple[str, ...]) -> Any:
    wanted = {_norm_key(c) for c in candidates}
    for k, v in obj.items():
        if _norm_key(k) in wanted and v not in (None, "", [], {}):
            return v
    return None


def _value_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        parts = [_value_to_text(v) for v in value]
        return "; ".join(p for p in parts if p)
    if isinstance(value, dict):
        # 常见 {"text": "..."} / {"def": "..."} 嵌套
        for k in ("text", "def", "definition", "meaning", "translation", "value", "content"):
            if k in value:
                text = _value_to_text(value[k])
                if text:
                    return text
        return "; ".join(
            f"{k}: {_value_to_text(v)}" for k, v in value.items() if _value_to_text(v)
        )
    return str(value).strip()


def _format_pron(value: Any) -> str:
    """发音字段可能是字符串，或 {"美": "...", "英": "..."} 形式。"""
    if isinstance(value, dict):
        parts = []
        for label, v in value.items():
            text = _value_to_text(v).strip()
            if text:
                parts.append(f"{label} {text}" if str(label).strip() else text)
        return " / ".join(parts)
    return _value_to_text(value)


def _walk_collect(node: Any, found: dict) -> None:
    """递归遍历 JSON，收集首个命中的 word/phonetic/meaning。"""
    if isinstance(node, dict):
        if not found.get("word"):
            w = _find_by_keys(node, WORD_KEYS)
            if w:
                found["word"] = _value_to_text(w)
        if not found.get("phonetic"):
            p = _find_by_keys(node, PHONETIC_KEYS)
            if p:
                found["phonetic"] = _format_pron(p)
        if not found.get("meaning"):
            m = _find_by_keys(node, MEANING_KEYS)
            if m:
                found["meaning"] = _value_to_text(m)
        for value in node.values():
            if not all(k in found for k in ("phonetic", "meaning")):
                _walk_collect(value, found)
    elif isinstance(node, list):
        for item in node:
            if not all(k in found for k in ("phonetic", "meaning")):
                _walk_collect(item, found)


def parse_kd_output(raw: str, queried_word: str = "") -> DictEntry | None:
    """kd.exe 输出兼容解析：先 JSON 启发式，再纯文本启发式。无法解析返回 None。"""
    if not raw or not raw.strip():
        return None
    text = raw.strip()

    # 帮助/用法错误文本不是释义
    if sum(1 for marker in _HELP_MARKERS if marker in text) >= 2:
        return None

    # 1) JSON（可能整体就是，也可能嵌在日志行里）
    json_candidates = [text]
    first_brace = text.find("{")
    first_bracket = text.find("[")
    starts = [p for p in (first_brace, first_bracket) if p >= 0]
    if starts:
        json_candidates.append(text[min(starts):])
    parsed_json = False
    for candidate in json_candidates:
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        parsed_json = True
        found: dict = {}
        _walk_collect(data, found)
        if found.get("phonetic") or found.get("meaning"):
            return DictEntry(
                word=found.get("word") or queried_word,
                phonetic=found.get("phonetic", "").strip(),
                meaning=found.get("meaning", "").strip(),
                source="kd",
            )
    if parsed_json:
        # 合法 JSON 但没有有效词条（如 kd 的空结果），即为未找到
        return None

    # 2) 纯文本启发式
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None
    phonetic = ""
    meaning_lines: list[str] = []
    for line in lines:
        # 守护进程状态行 / 例句行 不是释义
        if any(hint in line for hint in _NOISE_LINE_HINTS):
            continue
        if line.startswith(_EXAMPLE_LEADERS):
            continue

        if not phonetic:
            m = _SLASH_PHONETIC_RE.search(line)
            if not m:
                for candidate in _BRACKET_PHONETIC_RE.finditer(line):
                    if _looks_like_phonetic(candidate.group(0)):
                        m = candidate
                        break
            if m:
                phonetic = m.group(0)
                rest = (line[: m.start()] + " " + line[m.end():]).strip(" \t-—:：")
                # 行内除了音标还有实质内容（且不是单词本身）才保留
                if rest and rest.lower() != queried_word.lower():
                    meaning_lines.append(rest)
                continue

        if line.lower() == queried_word.lower():
            continue  # 首行回显单词，跳过
        meaning_lines.append(line)
    meaning = " ".join(meaning_lines)[:500].strip()
    if not phonetic and not meaning:
        return None
    return DictEntry(word=queried_word, phonetic=phonetic, meaning=meaning, source="kd")


def _decode_output(data: bytes) -> str:
    """kd.exe 可能输出 UTF-8（带/不带 BOM）或系统 ANSI(GBK)。"""
    if not data:
        return ""
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return data.decode("gbk", errors="replace")
        except LookupError:
            return data.decode("utf-8", errors="replace")


# ---------------------------------------------------------------- Providers
class DictionaryProvider(ABC):
    name: str = "base"

    @abstractmethod
    def lookup(self, word: str) -> DictEntry | None:
        """返回词条；查不到返回 None；失败抛异常由服务层统一降级。"""

    def available(self) -> bool:
        return True


class KdExeProvider(DictionaryProvider):
    name = "kd"

    def __init__(self, exe_path: Path | str = KD_EXE_PATH, timeout: float = KD_TIMEOUT_SEC,
                 runner: Callable | None = None) -> None:
        self.exe_path = Path(exe_path)
        self.timeout = timeout
        # runner 便于测试注入：runner(cmd, timeout) -> (stdout_bytes, stderr_bytes)
        self._runner = runner

    def available(self) -> bool:
        return self.exe_path.exists()

    def warmup(self) -> bool:
        """后台预热：用官方 --daemon 拉起 kd 守护进程。任何结果都不影响后续查询。"""
        if not self.available():
            log.info("kd.exe 不存在，跳过预热：%s", self.exe_path)
            return False
        try:
            if self._runner is not None:
                self._runner([str(self.exe_path), "--daemon"], WARMUP_TIMEOUT_SEC)
            else:
                subprocess.run(
                    [str(self.exe_path), "--daemon"],
                    capture_output=True,
                    timeout=WARMUP_TIMEOUT_SEC,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            log.info("kd.exe 预热完成")
            return True
        except subprocess.TimeoutExpired:
            # daemon 命令超时也可能意味着守护进程已被拉起，算可接受
            log.info("kd.exe 预热超时（守护进程可能已在运行）")
            return True
        except Exception as exc:
            log.warning("kd.exe 预热失败：%s", exc)
            return False

    def lookup(self, word: str) -> DictEntry | None:
        if not self.available():
            return None
        # 优先官方 JSON 接口（干净 UTF-8，无颜色码）；解析器仍兼容纯文本输出
        cmd = [str(self.exe_path), "--json", word]
        try:
            if self._runner is not None:
                out, err = self._runner(cmd, self.timeout)
            else:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    timeout=self.timeout,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                out, err = proc.stdout, proc.stderr
        except subprocess.TimeoutExpired:
            log.warning("kd.exe 查询超时（%ss）：%r", self.timeout, word)
            raise TimeoutError(f"kd.exe 查询超时：{word}")
        except OSError as exc:
            log.error("kd.exe 调用失败：%s", exc)
            raise

        text = _decode_output(out)
        if not text.strip() and err:
            text = _decode_output(err)
        entry = parse_kd_output(text, word)
        if entry is None:
            log.info("kd.exe 输出无法解析：%r -> %r", word, text[:80])
        else:
            entry.source = self.name
        return entry


class BuiltinJsonProvider(DictionaryProvider):
    name = "builtin"

    def __init__(self, json_path: Path | str = BUILTIN_DICT_PATH) -> None:
        self.json_path = Path(json_path)
        self._data: dict | None = None
        self._load_failed = False

    def _load(self) -> dict:
        if self._data is not None:
            return self._data
        try:
            raw = self.json_path.read_text(encoding="utf-8-sig")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("内置词库根节点不是 JSON 对象")
            self._data = data
        except FileNotFoundError:
            if not self._load_failed:
                log.error("内置词库不存在：%s", self.json_path)
            self._load_failed = True
            self._data = {}
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            if not self._load_failed:
                log.error("内置词库损坏/不可读：%s", exc)
            self._load_failed = True
            self._data = {}
        return self._data

    def lookup(self, word: str) -> DictEntry | None:
        data = self._load()
        record = data.get(word) or data.get(word.lower()) or data.get(word.strip().lower())
        if not isinstance(record, dict):
            return None
        entry = DictEntry(
            word=word,
            phonetic=str(record.get("phonetic", "")).strip(),
            meaning=str(record.get("meaning", "")).strip(),
            source=self.name,
        )
        return None if entry.is_empty() else entry


# ---------------------------------------------------------------- Service
class _LookupWorker(QThread):
    # token, DictEntry|None, status: found-source / none / timeout / error
    finishedOk = Signal(int, object, str)
    failed = Signal(int, str)

    def __init__(self, token: int, word: str, providers: list[DictionaryProvider],
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._token = token
        self._word = word
        self._providers = providers

    def run(self) -> None:
        try:
            entry: DictEntry | None = None
            saw_timeout = False
            saw_error = False
            for provider in self._providers:
                if self.isInterruptionRequested():
                    return
                if not provider.available():
                    continue
                try:
                    entry = provider.lookup(self._word)
                except TimeoutError:
                    saw_timeout = True
                    continue
                except Exception as exc:
                    saw_error = True
                    log.warning("词典 %s 查询异常，继续降级：%s", provider.name, exc)
                    continue
                if entry is not None:
                    log.info("词典命中：%r <- %s", self._word, provider.name)
                    self.finishedOk.emit(self._token, entry, entry.source)
                    return
            if entry is None:
                status = "timeout" if saw_timeout else ("error" if saw_error else "none")
            else:
                status = entry.source
            self.finishedOk.emit(self._token, entry, status)
        except Exception as exc:
            log.error("查询线程异常：%s", exc)
            self.failed.emit(self._token, f"{type(exc).__name__}: {exc}")


class DictionaryService(QObject):
    """级联查询 + 内存缓存 + 异步接口。"""

    resultReady = Signal(int, str, object, str)  # token, word, entry|None, source

    def __init__(self, providers: list[DictionaryProvider] | None = None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._providers = providers or [KdExeProvider(), BuiltinJsonProvider()]
        # word(lower) -> (entry|None, status)；None 为负缓存
        self._cache: dict[str, tuple[DictEntry | None, str]] = {}
        self._token = 0
        self._active: set[_LookupWorker] = set()

    # ---------------- 缓存 ----------------
    def cached(self, word: str) -> DictEntry | None:
        item = self._cache.get(word.strip().lower())
        return item[0] if item else None

    def _put_cache(self, word: str, entry: DictEntry | None, status: str) -> None:
        self._cache[word.strip().lower()] = (entry, status)

    # ---------------- 异步 ----------------
    def lookup_async(self, word: str) -> int:
        """发起查询，返回 token；结果经 resultReady 信号返回。"""
        word = (word or "").strip()
        self._token += 1
        token = self._token

        cached = self._cache.get(word.lower())
        if cached is not None:
            entry, status = cached
            from PySide6.QtCore import QTimer

            QTimer.singleShot(0, lambda: self.resultReady.emit(token, word, entry, status))
            return token

        worker = _LookupWorker(token, word, self._providers, parent=self)
        worker.finishedOk.connect(lambda t, e, s: self._on_ok(t, word, e, s))
        worker.failed.connect(lambda t, reason: self._on_fail(t, word, reason))

        def _cleanup() -> None:
            self._active.discard(worker)
            worker.deleteLater()

        worker.finished.connect(_cleanup)
        self._active.add(worker)
        worker.start()
        return token

    def _on_ok(self, token: int, word: str, entry: DictEntry | None, status: str) -> None:
        # 仅缓存明确结果；超时/临时失败允许下次重试
        if status in ("kd", "builtin", "none"):
            self._put_cache(word, entry, status)
        self.resultReady.emit(token, word, entry, status)

    def _on_fail(self, token: int, word: str, reason: str) -> None:
        # 线程级异常：不写负缓存，允许下次重试
        log.error("查询失败：%r %s", word, reason)
        self.resultReady.emit(token, word, None, f"error:{reason}")

    def stop_all(self) -> None:
        for worker in list(self._active):
            try:
                worker.requestInterruption()
                worker.wait(KD_TIMEOUT_SEC * 1000 + 500)
            except RuntimeError:
                pass
        self._active.clear()

    # ---------------- 预热 ----------------
    def warmup(self) -> None:
        """后台线程预热 kd.exe，绝不阻塞 UI。"""
        kd = next((p for p in self._providers if isinstance(p, KdExeProvider)), None)
        if kd is None:
            return

        def _run() -> None:
            try:
                kd.warmup()
            except Exception as exc:
                log.warning("预热线程异常：%s", exc)

        threading.Thread(target=_run, name="kd-warmup", daemon=True).start()
