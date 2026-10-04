"""LinguaPet 入口。

透明置顶桌宠 + 系统托盘 + 开机启动 + 单实例激活。
用法：
    python main.py            启动桌宠
    python main.py --check    自检后退出，打印 CHECK_OK
"""
from __future__ import annotations

import json
import sys
import threading

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from app import autostart, clipboard
from app.config import Config
from app.definition_popup import DefinitionPopup
from app.dictionary import DictionaryService
from app.hotkey import HotkeyManager
from app.logger import get_logger, setup_logging
from app.paths import BUILTIN_DICT_PATH, CONFIG_PATH, FROZEN, ICON_PATH, ensure_dirs
from app.pet_window import PetWindow
from app.single_instance import SingleInstance
from app.tray import TrayController
from app.tts import TtsManager
from app.wrong_book import WrongBook
from app.wrong_book_window import WrongBookWindow


def trim_working_set() -> bool:
    """请求 OS 回收物理工作集（EmptyWorkingSet）。

    桌宠长时间常驻，启动期产生的临时页/代码页大部分不会再被触碰；
    归还后任务管理器工作集明显下降，被访问时按需分页回来，体感无损。
    非 Windows 或调用失败时静默返回 False，绝不影响主流程。
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        # Win64 下必须声明 64 位 HANDLE 参数，否则伪句柄 -1 被截断成
        # 0xFFFFFFFF 导致 EmptyWorkingSet 静默失败
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        psapi.EmptyWorkingSet.argtypes = [ctypes.c_void_p]
        psapi.EmptyWorkingSet.restype = ctypes.c_int
        handle = kernel32.GetCurrentProcess()
        return bool(psapi.EmptyWorkingSet(handle))
    except Exception:
        return False


def install_excepthooks() -> None:
    """兜住主线程与工作线程未处理异常：只记日志，不崩进程。"""
    log = get_logger("excepthook")

    def sys_hook(exc_type, exc_value, exc_tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        log.critical("未捕获异常", exc_info=(exc_type, exc_value, exc_tb))

    def threading_hook(args: threading.ExceptHookArgs) -> None:
        log.error(
            "工作线程未捕获异常：thread=%s",
            args.thread.name if args.thread else "?",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = sys_hook
    threading.excepthook = threading_hook


def self_check() -> int:
    """自检：配置可加载、内置词库可解析、图标/注册表接口可用。"""
    log = get_logger("check")
    problems: list[str] = []

    cfg = Config()
    cfg.save()
    log.info("配置加载/保存正常：%s", CONFIG_PATH)

    if not BUILTIN_DICT_PATH.exists():
        problems.append(f"内置词库不存在：{BUILTIN_DICT_PATH}")
    else:
        try:
            data = json.loads(BUILTIN_DICT_PATH.read_text(encoding="utf-8"))
            if not data or not isinstance(data.get("apple"), dict):
                problems.append("内置词库结构异常")
            else:
                log.info("内置词库加载正常：%s 个词条", len(data))
        except (json.JSONDecodeError, OSError) as exc:
            problems.append(f"内置词库解析失败：{exc}")

    if not ICON_PATH.exists():
        problems.append(f"图标缺失：{ICON_PATH}")
    else:
        log.info("图标存在：%s", ICON_PATH.name)

    try:
        log.info("开机启动当前状态：%s", autostart.is_enabled())
    except Exception as exc:
        problems.append(f"注册表访问异常：{exc}")

    # TTS 链路
    try:
        import edge_tts

        log.info("edge-tts 在线合成模块可用")
    except Exception as exc:
        problems.append(f"edge-tts 不可用：{exc}")
    try:
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

        log.info("QtMultimedia 播放模块可用")
    except Exception as exc:
        problems.append(f"QtMultimedia 不可用：{exc}")
    try:
        import win32com.client

        sp = win32com.client.Dispatch("SAPI.SpVoice")
        log.info("SAPI5 离线嗓音数量：%s", sp.GetVoices().Count)
    except Exception as exc:
        problems.append(f"SAPI5 不可用：{exc}")

    log.info("运行模式：%s", "frozen(exe)" if FROZEN else "development")
    if problems:
        for p in problems:
            log.error("自检问题：%s", p)
        print("CHECK_FAIL: " + "; ".join(problems))
        return 1

    print("CHECK_OK")
    log.info("自检通过")
    return 0


def main() -> int:
    # 高 DPI 策略必须在创建 QApplication 前设置（Qt6 默认开启高 DPI）
    try:
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except Exception:  # 老版本/异常平台不支持时忽略
        pass

    app = QApplication(sys.argv)
    app.setApplicationName("LinguaPet")
    app.setQuitOnLastWindowClosed(False)  # 托盘常驻：关窗不退出

    ensure_dirs()
    log = setup_logging()
    install_excepthooks()
    log.info("LinguaPet 启动（Python %s, frozen=%s）", sys.version.split()[0], FROZEN)

    if not ICON_PATH.exists():
        log.warning("图标文件缺失：%s", ICON_PATH)
    app.setWindowIcon(QIcon(str(ICON_PATH)))

    if "--check" in sys.argv:
        return self_check()

    cfg = Config()
    cfg.save()

    # 单实例：第二实例通知本进程后自行退出
    guard = SingleInstance()
    if guard.is_second:
        log.info("已有实例运行，已通知其激活，本进程退出")
        return 0
    guard.acquire()

    # 桌宠
    pet = PetWindow(cfg)

    # 托盘
    tray = TrayController(config=cfg)
    tray.show()

    def toggle_pet() -> None:
        if pet.isVisible():
            pet.hide()
        else:
            pet.bring_to_front()

    tray.set_show_hide_handler(toggle_pet)

    def activate_existing() -> None:
        log.info("收到第二实例激活请求")
        pet.bring_to_front()
        tray.notify("LinguaPet", "我在这里～")

    guard.activated.connect(activate_existing)

    # ---------------- 全局热键（取词 / 加入错题本） ----------------
    hotkeys = HotkeyManager(
        {
            "capture": str(cfg.get("hotkey")),
            "add": str(cfg.get("add_hotkey")),
        }
    )

    def capture_selection(on_ready) -> None:
        """模拟 Ctrl+C，150ms 后一次性读取清洗结果：on_ready(word | None)。"""
        if not clipboard.send_copy():
            pet.show_word_hint("取词失败：无法模拟按键")
            on_ready(None)
            return
        QTimer.singleShot(
            clipboard.COPY_WAIT_MS, lambda: on_ready(clipboard.read_selection())
        )

    def on_capture() -> None:
        log.info("执行取词：发送 Ctrl+C")

        def got(word: str | None) -> None:
            if word:
                pet.set_current_word(word)
                pet.show_word_hint(word)
                log.info("取词成功：%r", word)
            else:
                # 保留上一个当前词，仅短暂提示
                pet.show_word_hint("未选中有效文本（保留上一个词）")

        capture_selection(got)

    def on_add_hotkey() -> None:
        """Ctrl+Alt+E：直接把选中文本加入错题本，释义后台补全。"""
        log.info("执行热键加词：发送 Ctrl+C")

        def got(word: str | None) -> None:
            if not word:
                pet.show_word_hint("未选中有效文本，无法加入错题本")
                tray.notify("LinguaPet", "未选中有效文本，无法加入错题本")
                return
            if wrong_book.exists(word):
                pet.show_word_hint(f"{word} 已在错题本中")
                tray.notify("LinguaPet", f"已在错题本中：{word}")
                return
            if wrong_book.add(word):
                enrich_pending.add(word.strip().lower())
                dict_service.lookup_async(word)  # 后台补全音标/释义
                pet.show_word_hint(f"已加入错题本：{word}")
                tray.notify("LinguaPet", f"已加入错题本：{word}")
                if wrong_window.isVisible():
                    wrong_window.refresh()
                log.info("热键加词成功：%r（等待释义补全）", word)
            else:
                tray.notify("LinguaPet", "写入错题本失败，详情见日志")

        capture_selection(got)

    @hotkeys.hotkeyPressed.connect
    def _on_hotkey(name: str) -> None:
        if name == "capture":
            on_capture()
        elif name == "add":
            on_add_hotkey()

    def on_hotkey_failed(name: str, reason: str) -> None:
        log.error("热键注册失败 %s：%s", name, reason)
        tray.notify("LinguaPet", f"热键注册失败：{reason}")

    hotkeys.registerFailed.connect(on_hotkey_failed)
    hotkeys.start()
    app.aboutToQuit.connect(hotkeys.stop)

    # ---------------- 朗读（在线女声优先，离线兜底） ----------------
    tts = TtsManager(cfg)

    def on_speak(word: str) -> None:
        if not word:
            pet.show_word_hint("先按 F2 取词，再单击朗读")
            return
        log.info("朗读请求：%r", word)
        tts.speak(word)

    @tts.stateChanged.connect
    def _on_tts_state(state: str) -> None:
        if state == "online_loading":
            pet.show_word_hint("正在合成朗读…")
        elif state == "offline":
            pet.show_word_hint("离线朗读（系统女声）")

    # ---------------- 查词 / 释义弹窗 / 错题本数据 ----------------
    dict_service = DictionaryService()
    dict_service.warmup()  # 后台预热 kd.exe，不阻塞启动
    app.aboutToQuit.connect(dict_service.stop_all)

    wrong_book = WrongBook()
    popup = DefinitionPopup()
    wrong_window = WrongBookWindow(wrong_book)
    wrong_window.speakRequested.connect(tts.speak)

    # 热键直加、等待词典后台补全的词条（小写 key）
    enrich_pending: set[str] = set()

    lookup_state = {"token": -1, "word": ""}

    def on_definition(word: str) -> None:
        if not word:
            pet.show_word_hint("先按 F2 取词，再双击查看释义")
            return
        log.info("请求释义：%r", word)
        popup.present(word, pet.frameGeometry(), in_book=wrong_book.exists(word))
        lookup_state["word"] = word
        lookup_state["token"] = dict_service.lookup_async(word)

    @dict_service.resultReady.connect
    def _on_dict_result(token: int, word: str, entry, status: str) -> None:
        # token 固化上下文：弹窗已关闭或已改查别的词时丢弃旧结果
        if token != lookup_state["token"] or not popup.isVisible():
            return
        if entry is not None:
            popup.show_entry(entry, already_in_book=wrong_book.exists(entry.word or word))
        else:
            log.info("未找到释义：%r（status=%s）", word, status)
            popup.show_missing(word, status)

    @dict_service.resultReady.connect
    def _enrich_wrong_entry(token: int, word: str, entry, status: str) -> None:
        """热键直加的词条：词典结果回来后补全音标/释义，不动排序与时间。"""
        key = word.strip().lower()
        if key not in enrich_pending:
            return
        enrich_pending.discard(key)
        if entry is not None and wrong_book.exists(word):
            wrong_book.update_info(word, entry.phonetic, entry.meaning)
            log.info("错题本词条已补全：%r（来源 %s）", word, entry.source)
            if wrong_window.isVisible():
                wrong_window.refresh()
        else:
            log.info("错题本词条 %r 无释义可补全（status=%s），保留单词", word, status)

    @popup.speakRequested.connect
    def _on_popup_speak(word: str) -> None:
        tts.speak(word)

    @popup.addRequested.connect
    def _on_popup_add(word: str, phonetic: str, meaning: str) -> None:
        ok = wrong_book.add(word, phonetic, meaning)
        if ok:
            popup.mark_added()
            tray.notify("LinguaPet", f"已加入错题本：{word}")
            if wrong_window.isVisible():
                wrong_window.refresh()
        else:
            tray.notify("LinguaPet", "写入错题本失败，详情见日志")

    pet.speakRequested.connect(on_speak)
    pet.definitionRequested.connect(on_definition)

    @tray.wrongBookRequested.connect
    def _on_wrong_book() -> None:
        log.info("打开错题本窗口")
        wrong_window.refresh()
        wrong_window.bring_to_front()

    def quit_app() -> None:
        log.info("收到退出指令，结束进程")
        tts.shutdown()
        dict_service.stop_all()
        tray.hide()
        app.quit()

    tray.quitRequested.connect(quit_app)

    pet.show()
    tray.notify(
        "LinguaPet",
        f"已启动：{cfg.get('hotkey')} 取词，{cfg.get('add_hotkey')} 加入错题本，"
        "单击朗读，双击查词",
    )
    # 常驻内存管理：启动 10s 后（预热/通知都结束）回收一次，之后每 5 分钟一次
    def _scheduled_trim() -> None:
        if trim_working_set():
            log.info("已回收工作集内存")

    QTimer.singleShot(10_000, _scheduled_trim)
    trim_timer = QTimer()
    trim_timer.setInterval(5 * 60 * 1000)
    trim_timer.timeout.connect(_scheduled_trim)
    trim_timer.start()

    log.info("桌宠已显示，进入事件循环")
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
