# AutoStory-Tool 🎮

> 基于 OpenCV 与 PyDirectInput 的通用游戏剧情自动化工具，支持多尺度图像识别、自定义监视区域、自定义按键与后台静默挂机。

## ✨ 核心特性
- **多图匹配**：支持同时监控多个目标图片。
- **多尺度识别**：支持自定义缩放匹配，适应不同分辨率和窗口大小。
- **双动作引擎**：支持自动按键盘按键（默认 F 键）或点击鼠标左键。
- **智能防误触**：鼠标离开选区自动静默，避免影响你做其他事情。
- **全局快捷键**：`Alt + P` 随时暂停/恢复，`Alt + Q` 安全退出。
- **优雅提示**：右下角系统级气泡通知，不抢占游戏焦点。

## 🚀 如何使用（普通玩家）
1. 前往本仓库的 **Releases** 页面下载最新的 `AutoStory.zip` 压缩包。
2. 解压后，将你需要识别的游戏截图放入 `F_Picture` 或 `Left_Picture` 文件夹。
3. 右键 `AutoStory.exe`，选择 **“以管理员身份运行”**（必须）。
4. 按照弹窗提示，框选游戏监视区域，等待 4 秒倒计时即可开始。

> ⚠️ **警告**：由于本软件使用 PyInstaller 打包且需要管理员权限，Windows Defender 或 360 极有可能误报。请将其加入白名单后再运行，本工具绝不会窃取任何隐私。

## 系统要求：目前仅支持 Windows 10 / 11 64位系统。由于底层依赖 Windows API，暂不支持 Mac 或 Linux。

## 💻 源码运行与打包（开发者）
如果你有 Python 环境（推荐 3.12），可以自行运行和打包：

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 运行源码
python AutoStory.py

# 3. 自行打包为单文件 exe (无控制台)
python -m PyInstaller --onefile --noconsole --uac-admin --clean --noconfirm --name "AutoStory" --hidden-import=pyscreeze --hidden-import=PIL --hidden-import=pydirectinput --hidden-import=keyboard --hidden-import=mss AutoStory.py