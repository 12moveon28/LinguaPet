# -*- mode: python ; coding: utf-8 -*-
"""LinguaPet PyInstaller 打包配置（Windows onedir）。

构建：
    venv\\Scripts\\python.exe -m PyInstaller LinguaPet.spec --noconfirm
产物：
    dist\\LinguaPet\\          程序目录（整个文件夹分发）
    dist\\LinguaPet\\LinguaPet.exe   启动程序
    可写数据（config.json / data / logs）写在 LinguaPet.exe 同级目录，便携
注意：
    kd.exe 不随包发布，需手动放到 dist\\LinguaPet\\ 内（与 exe 同目录）。

依赖事实（2026-09 核对）：
- edge_tts / win32com / pynput / pyperclip 均为函数内延迟导入，
  必须在 hiddenimports 显式声明，否则 onefile 内 ModuleNotFoundError。
- 代码不使用 PIL(Pillow) 与 pyttsx3（离线朗读直接走 win32com SAPI），
  已在 excludes 排除以控制体积。
- 仅用 Qt Core/Gui/Widgets/Network/Multimedia，其余 Qt 大模块全部排除。
"""
from PyInstaller.utils.hooks import collect_submodules

# ---------------- 隐式导入（延迟导入 + 动态后端） ----------------
hiddenimports: list[str] = []

# edge-tts 在 QThread 内 import；收集全部子模块，
# 其顶层依赖（aiohttp/certifi 等）会被 Analysis 继续追踪并触发官方 hook
hiddenimports += collect_submodules("edge_tts")

# pynput Windows 低级钩子后端 + pyperclip（均为函数内 import）
hiddenimports += [
    "pynput",
    "pynput.keyboard",
    "pynput.keyboard._win32",
    "pynput.mouse",
    "pynput.mouse._win32",
    "pynput._util",
    "pynput._util.win32",
    "pynput._util.darwin",  # 跨平台包结构补齐（Windows 下不加载也无害）
    "pynput._util.xorg",
    "pyperclip",
    # pywin32：SAPI5 离线朗读（win32com.client.Dispatch）
    "win32com",
    "win32com.client",
    "pythoncom",
    "pywintypes",
]

# ---------------- 排除（代码确认未使用，显著减小体积） ----------------
excludes = [
    # 未使用的第三方库
    "PIL",
    "pyttsx3",
    "comtypes",
    "numpy",
    "pandas",
    "matplotlib",
    "PyQt5",
    "PyQt6",
    "IPython",
    "jupyter",
    "notebook",
    "pytest",
    # 标准库大件（桌宠用不到）
    "tkinter",
    "unittest",
    "test",
    "pydoc",
    "doctest",
    "lib2to3",
    # ---- 未使用的 Qt 模块（保留 Core/Gui/Widgets/Network/Multimedia） ----
    "PySide6.QtQml",
    "PySide6.QtQmlModels",
    "PySide6.QtQmlWorkerScript",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickWidgets",
    "PySide6.QtQuickControls2",
    "PySide6.QtQuickTemplates2",
    "PySide6.QtQuickLayouts",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel",
    "PySide6.QtWebSockets",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DExtras",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtPositioning",
    "PySide6.QtLocation",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtTest",
    "PySide6.QtUiTools",
    "PySide6.QtSql",
    "PySide6.QtPrintSupport",
    "PySide6.QtSvg",
    "PySide6.QtSvgWidgets",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtScxml",
    "PySide6.QtStateMachine",
    "PySide6.QtRemoteObjects",
    "PySide6.QtVirtualKeyboard",
    "PySide6.QtTextToSpeech",
    "PySide6.QtHttpServer",
    "PySide6.QtJsonRpc",
    "PySide6.QtAxContainer",
    "PySide6.QtConcurrent",
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[
        # 随包只读资源 → 运行时 sys._MEIPASS 下同名相对路径
        ("data/builtin_dict.json", "data"),
        ("assets/icon.ico", "assets"),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

# 纯 Python 模块字节码归档（缺失会导致运行时
# "[PYI:ERROR] PYZ archive entry not found in the TOC!"，必须显式构建并传入 EXE）
pyz = PYZ(a.pure)

# onedir：产物 dist/LinguaPet/（免 TEMP 解压，启动快、工作集小、杀软误报少）
exe = EXE(
    # 顺序必须与官方 onedir 模板一致：pyz 在前、scripts 在后，
    # 否则 CArchive 中运行时钩子先于 PYZ 导入器执行，
    # 报 "No module named 'inspect'"（pyi_rth_inspect 失败）
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LinguaPet",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,      # GUI 程序：无控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="LinguaPet",
)
