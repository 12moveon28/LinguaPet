"""TTS 朗读：在线 edge-tts 神经女声优先，SAPI5 离线兜底。

链路：
  speak(text)
    ├─ 先停止当前一切发声（QMediaPlayer + SAPI purge），保证"再点先停上一次"
    ├─ 命中本地 mp3 缓存 → QMediaPlayer 直接播放（断网也能读）
    ├─ online_tts=True → QThread 内 asyncio 合成（5s 超时）→ 成功落缓存再播放
    └─ 未开启在线 / 合成失败 / 超时 → SAPI5 英文女声（Zira）异步朗读

设计要点：
- 网络合成在 QThread，GUI 不阻塞；主线程持有 QMediaPlayer 播放。
- 自增 token 丢弃过期合成结果，快速连点不会让旧词覆盖新词。
- 离线不用 pyttsx3.runAndWait()（实测阻塞且无法跨线程打断），
  直接用其底层 SAPI.SpVoice 的 Async + PurgeBeforeSpeak 标志，可即时打断；
  嗓音/语速选择与 pyttsx3 的 SAPI5 驱动完全同源。
- 任何环节失败只记日志 + 降级，绝不抛到 UI。
"""
from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QUrl, Signal

from .logger import get_logger
from .paths import CACHE_DIR

log = get_logger("tts")

SYNTH_TIMEOUT_SEC = 5.0
# edge-tts 语速百分比（160 wpm 视为 0%）
RATE_NORMAL_WPM = 160


def wpm_to_edge_pct(rate_wpm: int) -> str:
    pct = round((int(rate_wpm) - RATE_NORMAL_WPM) / RATE_NORMAL_WPM * 100)
    pct = max(-50, min(100, pct))
    return f"{'+' if pct >= 0 else ''}{pct}%"


def wpm_to_sapi_rate(rate_wpm: int) -> int:
    return max(-10, min(10, round((int(rate_wpm) - RATE_NORMAL_WPM) / 16)))


class EdgeSynthWorker(QThread):
    """后台线程：edge-tts 合成 mp3 到临时文件，成功后原子改名。"""

    succeeded = Signal(int, str)   # token, mp3_path
    failed = Signal(int, str)      # token, reason

    def __init__(
        self,
        token: int,
        text: str,
        voice: str,
        rate_pct: str,
        out_path: Path,
        timeout: float = SYNTH_TIMEOUT_SEC,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._token = token
        self._text = text
        self._voice = voice
        self._rate_pct = rate_pct
        self._out_path = out_path
        self._timeout = timeout

    def run(self) -> None:
        tmp_path = self._out_path.with_suffix(".mp3.tmp")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            import edge_tts

            async def synth() -> None:
                communicate = edge_tts.Communicate(
                    self._text, self._voice, rate=self._rate_pct
                )
                await communicate.save(str(tmp_path))

            loop.run_until_complete(asyncio.wait_for(synth(), timeout=self._timeout))

            if self.isInterruptionRequested():
                self._safe_unlink(tmp_path)
                return
            if not tmp_path.exists() or tmp_path.stat().st_size == 0:
                raise RuntimeError("合成结果为空文件")
            os.replace(tmp_path, self._out_path)
            self.succeeded.emit(self._token, str(self._out_path))
        except Exception as exc:
            self._safe_unlink(tmp_path)
            if not self.isInterruptionRequested():
                self.failed.emit(self._token, f"{type(exc).__name__}: {exc}")
        finally:
            try:
                loop.close()
            except Exception:
                pass

    @staticmethod
    def _safe_unlink(path: Path) -> None:
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass


class OfflineSapiSpeaker(QObject):
    """SAPI5 离线朗读（与 pyttsx3.sapi5 同源），异步发声、可即时打断。"""

    SVSFlagsAsync = 1
    SVSFPurgeBeforeSpeak = 32
    SVSFIsFilename = 4

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._voice = None
        self._available = False

    def _ensure(self) -> bool:
        if self._voice is not None:
            return self._available
        try:
            import win32com.client  # pywin32（pyttsx3 的同依赖）

            sp = win32com.client.Dispatch("SAPI.SpVoice")
            # 优先英语女声；系统无英语嗓音时退回默认嗓音
            tokens = sp.GetVoices("Language=409;Gender=Female", "")
            if tokens.Count > 0:
                chosen = tokens.Item(0)
                # 多个英语女声时偏好 Zira
                for i in range(tokens.Count):
                    if "Zira" in tokens.Item(i).GetDescription():
                        chosen = tokens.Item(i)
                        break
                sp.Voice = chosen
                log.info("离线朗读音色：%s", chosen.GetDescription())
            else:
                log.warning("未找到英语女声，使用系统默认嗓音")
            self._voice = sp
            self._available = True
        except Exception as exc:
            self._available = False
            log.error("SAPI5 离线朗读初始化失败：%s", exc)
        return self._available

    def speak(self, text: str, rate_wpm: int, volume: float) -> bool:
        if not self._ensure():
            return False
        try:
            self._voice.Rate = wpm_to_sapi_rate(rate_wpm)
            self._voice.Volume = int(max(0.0, min(1.0, float(volume))) * 100)
            # Async + 先清空队列：天然实现"打断上一次"
            self._voice.Speak(
                text, self.SVSFlagsAsync | self.SVSFPurgeBeforeSpeak
            )
            return True
        except Exception as exc:
            log.error("SAPI5 朗读失败：%s", exc)
            return False

    def stop(self) -> None:
        if self._voice is None:
            return
        try:
            self._voice.Speak("", self.SVSFPurgeBeforeSpeak)
        except Exception as exc:
            log.debug("SAPI5 停止异常（可忽略）：%s", exc)


class TtsManager(QObject):
    """对外统一门面：speak / stop；内部编排在线合成、缓存、离线降级。"""

    stateChanged = Signal(str)  # online_loading / online / offline / idle

    def __init__(self, config, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._token = 0
        self._last_text = ""
        self._worker: EdgeSynthWorker | None = None
        self._active_workers: set[EdgeSynthWorker] = set()

        # 多媒体后端（Windows Media Foundation）实测一经实例化即占约 7MB，
        # 延迟到首次实际播放时再创建：纯待机不占这份内存
        self._audio_output = None
        self._player = None

        self._offline = OfflineSapiSpeaker(self)

    def _ensure_player(self):
        """首次播放时惰性创建 QMediaPlayer/QAudioOutput。"""
        if self._player is not None:
            return self._player, self._audio_output
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

        self._audio_output = QAudioOutput(self)
        self._player = QMediaPlayer(self)
        self._player.setAudioOutput(self._audio_output)
        self._player.errorOccurred.connect(self._on_player_error)
        log.info("多媒体播放后端已惰性加载")
        return self._player, self._audio_output

    # ---------------- 对外 ----------------
    def speak(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            log.info("朗读内容为空，忽略")
            return

        self._token += 1
        token = self._token
        self._last_text = text
        self.stop()

        rate = int(self._config.get("tts_rate", 160))
        volume = float(self._config.get("tts_volume", 1.0))
        # 仅在即将播放时才惰性拉起多媒体后端
        if self._audio_output is not None:
            self._audio_output.setVolume(max(0.0, min(1.0, volume)))

        if bool(self._config.get("online_tts", True)):
            cache = self._cache_path(text)
            if cache.exists() and cache.stat().st_size > 0:
                log.info("缓存命中：%s", cache.name)
                self._play_file(cache)
                self.stateChanged.emit("online")
                return
            self._start_synth(token, text, rate, cache)
        else:
            self._speak_offline(text, rate, volume)

    def stop(self) -> None:
        """停止当前在线播放与离线朗读；进行中的合成结果将被 token 丢弃。"""
        if self._player is not None:
            try:
                self._player.stop()
            except Exception as exc:
                log.debug("QMediaPlayer 停止异常：%s", exc)
        self._offline.stop()
        for worker in list(self._active_workers):
            try:
                worker.requestInterruption()
            except RuntimeError:
                # C++ 对象已析构（理论上 _clear_ref 已移除，兜底保护）
                self._active_workers.discard(worker)

    def shutdown(self) -> None:
        self.stop()
        for worker in list(self._active_workers):
            try:
                worker.wait(2000)
            except RuntimeError:
                pass
        self._active_workers.clear()

    # ---------------- 在线合成 ----------------
    def _cache_path(self, text: str) -> Path:
        voice = str(self._config.get("edge_voice"))
        digest = hashlib.sha1(f"{voice}|{text.lower()}".encode("utf-8")).hexdigest()[:16]
        safe_voice = voice.replace(" ", "_")
        return CACHE_DIR / f"{safe_voice}_{digest}.mp3"

    def _start_synth(self, token: int, text: str, rate_wpm: int, out_path: Path) -> None:
        voice = str(self._config.get("edge_voice"))
        rate_pct = wpm_to_edge_pct(rate_wpm)
        worker = EdgeSynthWorker(token, text, voice, rate_pct, out_path, parent=self)
        worker.succeeded.connect(self._on_synth_ok)
        worker.failed.connect(self._on_synth_failed)

        def _clear_ref() -> None:
            self._active_workers.discard(worker)
            if self._worker is worker:
                self._worker = None
            worker.deleteLater()

        worker.finished.connect(_clear_ref)
        # 旧 worker 停止追踪引用（结果靠 token 丢弃）
        self._worker = worker
        self._active_workers.add(worker)
        self.stateChanged.emit("online_loading")
        log.info("在线合成：%r voice=%s rate=%s", text, voice, rate_pct)
        worker.start()

    def _on_synth_ok(self, token: int, path: str) -> None:
        if token != self._token:
            log.info("丢弃过期合成结果 token=%s", token)
            return
        self._play_file(Path(path))
        self.stateChanged.emit("online")

    def _on_synth_failed(self, token: int, reason: str) -> None:
        if token != self._token:
            return
        log.warning("在线合成失败，降级离线朗读：%s", reason)
        rate = int(self._config.get("tts_rate", 160))
        volume = float(self._config.get("tts_volume", 1.0))
        text = getattr(self, "_last_text", "")
        if text:
            self._speak_offline(text, rate, volume)

    # ---------------- 播放 / 离线 ----------------
    def _play_file(self, path: Path) -> None:
        log.info("播放：%s", path)
        player, audio_output = self._ensure_player()
        volume = float(self._config.get("tts_volume", 1.0))
        audio_output.setVolume(max(0.0, min(1.0, volume)))
        player.setSource(QUrl.fromLocalFile(str(path.resolve())))
        player.play()

    def _on_player_error(self, error, message: str = "") -> None:
        # 播放失败不自动降级，避免合成音+离线音叠加；只记录
        log.error("音频播放失败 error=%s message=%s", error, message)

    def _speak_offline(self, text: str, rate_wpm: int, volume: float) -> None:
        ok = self._offline.speak(text, rate_wpm, volume)
        self.stateChanged.emit("offline" if ok else "idle")
        if not ok:
            log.error("离线朗读也不可用：%r", text)
