# LinguaPet 单词桌宠

Windows 桌面背单词小宠物：全局热键抓取选中的单词 → 朗读发音 → 双击查看音标/释义 → 一键收录错题本。常驻系统托盘，开机自启，便携免安装。

## 功能

- **全局热键取词**：任意软件中选中单词，按 `F2` 即可抓取（通过模拟 Ctrl+C 读取选中内容，会覆盖剪贴板）。
- **单词朗读**：在线 edge-tts 神经女声（`en-US-AnaNeural`，甜美少女音）优先，断网/失败自动降级为 Windows 系统离线女声（SAPI5 Zira）；合成结果本地缓存，再次朗读零流量。
- **音标释义弹窗**：双击桌宠查看当前单词，优先调用 `kd.exe`（`--json` 模式，2 秒超时保护），不可用时回退内置 51 词内置词库。
- **错题本**：`Ctrl+Alt+E` 直接把选中单词加入错题本，释义后台自动补全；错题本窗口支持搜索、双击朗读、右键删除。
- **桌面交互**：左键拖动桌宠，单击朗读当前词，双击查释义，位置自动记忆。
- **托盘常驻**：关闭窗口不退出；菜单支持显示/隐藏、打开错题本、开机启动、退出。
- **单实例保护**：重复启动会激活已有实例，不会开多个。
- **低内存**：多媒体后端按需加载，启动 10 秒后自动回收工作集；实测常驻工作集约 3–8MB、私有内存约 35MB。

## 快速开始（使用打包成品）

1. 获取整个 `LinguaPet` 文件夹（包含 `LinguaPet.exe` 与 `_internal` 目录，**二者必须在一起**）。
2. 将词典程序 **`kd.exe` 手动放到 `LinguaPet.exe` 同目录**（该程序不随包分发）：

   ```
   LinguaPet/
   ├── LinguaPet.exe      ← 双击运行这个
   ├── kd.exe             ← 需自行放入
   ├── _internal/         ← 运行时依赖，勿删勿移动
   ├── config.json        ← 首次运行自动生成
   ├── data/              ← 错题本与语音缓存（自动生成）
   └── logs/              ← 运行日志（自动生成）
   ```

3. 双击 `LinguaPet.exe`，桌面出现桌宠并弹出启动提示即成功。
4. 所有数据写在程序目录内，整个文件夹复制走即可迁移/卸载（直接删文件夹即可）。

> 注意：`build\` 目录是打包过程的中间产物，里面的 exe **不能运行**，可直接删除。

### 自检排错

程序异常时，在程序目录打开终端运行：

```
LinguaPet.exe --check
```

会依次检查配置、内置词库、图标、edge-tts、QtMultimedia、SAPI5 离线嗓音，全部通过打印 `CHECK_OK`。

## 使用说明

| 操作 | 效果 |
| --- | --- |
| 选中单词后按 `F2` | 抓取单词，桌宠显示提示 |
| 选中单词后按 `Ctrl+Alt+E` | 直接加入错题本（释义后台补全） |
| 单击桌宠 | 朗读当前单词（再点一次会打断上一次重新读） |
| 双击桌宠 | 弹出当前单词的音标/释义，可在弹窗内加入错题本 |
| 左键拖动桌宠 | 移动位置，自动保存 |
| 右键托盘图标 | 显示/隐藏、错题本、开机启动、退出 |

错题本窗口：输入框实时搜索；双击词条朗读；右键词条删除。

## 配置文件 config.json

首次运行自动生成，可用记事本编辑（保存为 UTF-8），改完重启程序生效：

| 键 | 默认值 | 说明 |
| --- | --- | --- |
| `hotkey` | `"f2"` | 取词热键，pynput 风格，如 `"f2"`、`"ctrl+alt+w"` |
| `add_hotkey` | `"ctrl+alt+e"` | 加入错题本热键 |
| `online_tts` | `true` | `true` 在线女声优先；`false` 只用系统离线朗读 |
| `edge_voice` | `"en-US-AnaNeural"` | edge-tts 嗓音名 |
| `tts_rate` | `160` | 语速（wpm），160 为正常语速 |
| `tts_volume` | `1.0` | 音量 0.0–1.0 |
| `pet_pos` | `[100, 100]` | 桌宠位置，一般无需手改 |
| `autostart` | `false` | 是否开机启动，也可在托盘菜单切换 |

配置损坏时程序会自动备份原文件（`config.json.corrupt-时间戳`）并重建默认配置，不会崩溃。

## 数据与日志

- `data/wrong_words.json`：错题本，结构为 `[{"word","phonetic","meaning","added_at"}]`，自动去重。
- `data/tts_cache/`：在线合成的 mp3 缓存，可随时删除，下次朗读重新下载。
- `logs/lingua_pet.log`：按 1MB×3 份轮转，出问题先看这里。

## 从源码运行 / 打包

环境要求：Windows 10/11，Python 3.10+（开发验证环境为 Python 3.14 + PyInstaller 6.22）。

```powershell
python -m venv venv
.\venv\Scripts\pip install -r requirements.txt

# 开发运行
.\venv\Scripts\python.exe main.py

# 打包（onedir）
.\venv\Scripts\python.exe -m PyInstaller LinguaPet.spec --noconfirm
# 产物在 dist\LinguaPet\，再把 kd.exe 拷进去
```

## 常见问题

- **双击 exe 提示 “Failed to load Python DLL”**：运行的是 `build\` 中间产物，或 `_internal` 文件夹不在 exe 旁边。请运行 `dist\LinguaPet\LinguaPet.exe`，并保持文件夹完整。
- **热键无反应**：热键可能被其他软件占用，托盘会弹出“热键注册失败”通知；修改 `config.json` 中的热键后重启。
- **朗读是系统机械音 / 提示离线朗读**：在线合成需要联网，5 秒超时或无网络时自动使用 SAPI5 女声，功能不受影响。
- **查不到释义**：确认 `kd.exe` 已放在 exe 同目录；即便缺失，也会自动回退内置词库（词汇量有限）。
- **杀毒软件拦截**：PyInstaller 打包程序偶被误报，添加信任即可；本程序所有数据仅保存在本地。

## 许可证

本项目代码以 [MIT 许可证](LICENSE) 发布。

### 第三方依赖

| 依赖 | 许可证 |
| --- | --- |
| [PySide6](https://www.qt.io/licensing) | LGPL-3.0 / GPL-2.0 / GPL-3.0 三选一 |
| [pynput](https://github.com/moses-palmer/pynput) | LGPLv3 |
| [edge-tts](https://github.com/rany2/edge-tts) | LGPLv3（`srt_composer.py` 为 MIT） |
| [pyperclip](https://github.com/asweigart/pyperclip) | BSD |
| [pywin32](https://github.com/mhammond/pywin32) | PSF |
| [PyInstaller](https://pyinstaller.org/) | GPLv2+，附例外条款允许打包分发非自由程序 |

以上均为宽松或 LGPL 许可，无 GPL 传染性；PySide6 / pynput / edge-tts 以动态导入方式使用，符合 LGPL 要求。

`kd.exe` 为第三方词典程序，许可证不明，**不随本仓库分发**，请自行获取并放入程序目录。

### 成品分发

打包产物（`dist/LinguaPet/`）不入库，通过 [Releases](../../releases) 页面以压缩包形式提供。由于 `kd.exe` 的版权归属不明，发布附件中**不含**该程序，运行前请自行放入。
