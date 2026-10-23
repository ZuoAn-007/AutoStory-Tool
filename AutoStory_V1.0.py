# -*- coding: utf-8 -*-
"""
自动剧情工具（V1.0）
基于 OpenCV 模板匹配 + PyDirectInput 底层输入模拟
启动方式：以管理员权限运行（打包时用 --uac-admin）
"""

import sys
import os
import time
import glob
import ctypes
import logging
import threading
from queue import Queue, Empty

import cv2
import numpy as np
import pyautogui
import pydirectinput
import keyboard
import mss
import tkinter as tk
from tkinter import messagebox


# ================= 全局运行状态 =================
STATE = {"paused": False, "running": True}
NOTIFY_QUEUE = Queue()


# ================= 路径基准（防止提权后工作目录跑到 System32） =================
BASE_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
CONFIG_FILE = os.path.join(BASE_DIR, "config.txt")
LOG_FILE = os.path.join(BASE_DIR, "auto_story.log")

DEFAULT_CONFIG_TEXT = """==================================================
            自动剧情工具V1.0 配置文件
==================================================
提示：修改冒号（:）后面的数值或文字即可。

--------------------------------------------------
【1. 相似度阈值】(0.1~1.0)
如果发现误触，请提高到 0.85 或 0.9。
--------------------------------------------------
CONFIDENCE: 0.85

--------------------------------------------------
【2. 扫描间隔时间】(秒)
--------------------------------------------------
CHECK_INTERVAL: 0.5

--------------------------------------------------
【3. 触发冷却时间】(秒)
--------------------------------------------------
COOLDOWN: 1.0

--------------------------------------------------
【4. 匹配尺度列表】(英文逗号分隔)
分辨率固定时只留 1.0 即可，速度最快。
--------------------------------------------------
SCALES: 0.9,1.0,1.1

--------------------------------------------------
【5. 按键动作】(匹配 F_Picture 时按下的键)
--------------------------------------------------
PRESS_KEY: f

--------------------------------------------------
【6. 动作后等待】(秒)
--------------------------------------------------
ACTION_DELAY: 1.0

--------------------------------------------------
【7. 点击位置】center=区域中心 match=图标位置
   current=鼠标当前位置(不移动)
   仅对无前缀的左键图片生效。
   文件名前缀可单张覆盖：
     match_图A.png   -> 点图标位置
     current_图B.png -> 在鼠标当前位置点击
--------------------------------------------------
CLICK_MODE: center

--------------------------------------------------
【8. 是否要求鼠标在选区内】1=是 0=否
--------------------------------------------------
REQUIRE_MOUSE_IN_REGION: 1

--------------------------------------------------
【9. 是否显示心跳输出】1=是 0=否
--------------------------------------------------
HEARTBEAT: 1

--------------------------------------------------
【10. 快捷键】暂停/继续、退出程序
--------------------------------------------------
HOTKEY_PAUSE: alt+p
HOTKEY_EXIT: alt+q
"""

DEFAULTS = {
    "CONFIDENCE": 0.85,
    "CHECK_INTERVAL": 0.5,
    "COOLDOWN": 2.0,
    "SCALES": "0.9,1.0,1.1",
    "PRESS_KEY": "f",
    "ACTION_DELAY": 1.0,
    "CLICK_MODE": "center",
    "REQUIRE_MOUSE_IN_REGION": 1,
    "HEARTBEAT": 1,
    "HOTKEY_PAUSE": "alt+p",
    "HOTKEY_EXIT": "alt+q",
}


# ================= 基础设施 =================

def enable_dpi_awareness():
    """必须在创建任何窗口/截图之前调用，
    否则高缩放屏幕下截图尺寸与点击坐标会被系统虚拟化换算"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # Per-Monitor V2
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def setup_logging():
    logger = logging.getLogger("auto_story")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("[%(asctime)s] %(message)s", "%H:%M:%S")

    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    if sys.stdout is not None:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(fmt)
        logger.addHandler(ch)
    return logger


def console_print(msg, end="\n"):
    """控制台输出兜底：windowed 打包 / 编码异常时不再崩溃"""
    if sys.stdout is None:
        return
    try:
        print(msg, end=end, flush=True)
    except Exception:
        pass


def ensure_single_instance():
    kernel32 = ctypes.windll.kernel32
    mutex = kernel32.CreateMutexW(None, False, "Global\\AutoStoryTool_Mutex_v3")
    if kernel32.GetLastError() == 183:   # ERROR_ALREADY_EXISTS
        return None
    return mutex


# ================= 通知（右下角气泡，不抢焦点） =================

def notify(title, message):
    NOTIFY_QUEUE.put((title, message))


def _show_notification(root, title, message):
    win = tk.Toplevel(root)
    win.overrideredirect(True)          # 无边框，创建时不接管输入焦点
    win.attributes("-topmost", True)
    w, h = 320, 92
    x = root.winfo_screenwidth() - w - 24
    y = root.winfo_screenheight() - h - 64
    win.geometry(f"{w}x{h}+{x}+{y}")

    frame = tk.Frame(win, bg="#1f1f1f", highlightbackground="#4a4a4a", highlightthickness=1)
    frame.pack(fill="both", expand=True)
    tk.Label(frame, text=title, bg="#1f1f1f", fg="#e8c547",
             font=("Microsoft YaHei", 10, "bold"), anchor="w").pack(fill="x", padx=12, pady=(10, 2))
    tk.Label(frame, text=message, bg="#1f1f1f", fg="#dddddd",
             font=("Microsoft YaHei", 9), anchor="w", justify="left",
             wraplength=w - 24).pack(fill="x", padx=12)
    win.after(2200, win.destroy)


# ================= 状态切换（热键线程调用） =================

def toggle_pause():
    STATE["paused"] = not STATE["paused"]
    if STATE["paused"]:
        notify("自动剧情工具", "监视已暂停（快捷键恢复）")
    else:
        notify("自动剧情工具", "监视已恢复")


def request_exit():
    if not STATE["running"]:
        return
    STATE["running"] = False
    # 延迟 2 秒再销毁窗口，让气泡有时间显示
    threading.Timer(2.0, lambda: NOTIFY_QUEUE.put(("__QUIT__", None))).start()
    notify("自动剧情工具", "正在停止监视...")


# ================= 配置读写（逐行容错） =================

def load_config(logger):
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            f.write(DEFAULT_CONFIG_TEXT)

    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            for lineno, raw in enumerate(f, 1):
                line = raw.strip()
                if not line or line[0] in "-=【#":
                    continue
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                key, value = key.strip().upper(), value.strip()
                if key not in DEFAULTS:
                    continue
                try:
                    default = DEFAULTS[key]
                    if key == "SCALES":
                        scales = [float(x) for x in value.split(",") if x.strip()]
                        cfg[key] = scales if scales else [1.0]
                    elif isinstance(default, float):
                        cfg[key] = float(value)
                    elif isinstance(default, int):
                        cfg[key] = int(value)
                    else:
                        cfg[key] = value
                except Exception:
                    logger.warning("配置文件第 %d 行解析失败，已忽略：%s", lineno, line)
    except Exception as e:
        logger.warning("读取配置文件失败，使用默认值：%s", e)

    # 值合法性收敛
    if not str(cfg["PRESS_KEY"]).strip():
        cfg["PRESS_KEY"] = "f"
    if str(cfg["CLICK_MODE"]).strip().lower() not in ("center", "match", "current"):
        cfg["CLICK_MODE"] = "center"
    if cfg["CONFIDENCE"] <= 0 or cfg["CONFIDENCE"] > 1:
        cfg["CONFIDENCE"] = DEFAULTS["CONFIDENCE"]
    return cfg


# ================= 界面流程 =================

def show_startup_dialog(cfg):
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    msg = (
        "欢迎使用自动剧情工具！\n\n"
        "【图片准备说明】\n"
        "1. 想要按「%s」键的截图，请放入 'F_Picture' 文件夹。\n"
        "2. 想要点击鼠标的截图，请放入 'Left_Picture' 文件夹。\n"
        "   文件名前缀可指定点击方式：\n"
        "   match_图A.png = 点图标位置 | current_图B.png = 原地点击\n"
        "   无前缀 = 使用配置文件的 CLICK_MODE 设置\n\n"
        "【快捷键说明】\n"
        "【%s】暂停/恢复  【%s】退出程序\n\n"
        "点击「确定」后，请框选游戏监视区域。"
    ) % (cfg["PRESS_KEY"].upper(), cfg["HOTKEY_PAUSE"].upper(), cfg["HOTKEY_EXIT"].upper())
    result = messagebox.askokcancel("启动确认", msg)
    root.destroy()
    return result


def show_error(title, message):
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    messagebox.showerror(title, message)
    root.destroy()


def select_region():
    root = tk.Tk()
    root.attributes("-fullscreen", True)
    root.attributes("-alpha", 0.3)
    root.configure(bg="black")
    root.attributes("-topmost", True)
    canvas = tk.Canvas(root, cursor="cross", bg="black", highlightthickness=0)
    canvas.pack(fill="both", expand=True)

    coords, state = {}, {"rect": None, "text": None, "cancelled": False}
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    canvas.create_text(sw // 2, sh // 2 - 50,
                       text="按住鼠标左键拖拽框选游戏区域\n松开鼠标完成\n(按 Esc 键取消)",
                       fill="white", font=("Microsoft YaHei", 18, "bold"), justify="center")

    def on_press(event):
        coords["x1"], coords["y1"] = event.x, event.y

    def on_drag(event):
        if state["rect"]:
            canvas.delete(state["rect"])
        if state["text"]:
            canvas.delete(state["text"])
        state["rect"] = canvas.create_rectangle(
            coords["x1"], coords["y1"], event.x, event.y,
            outline="red", width=3, fill="white")
        w = abs(event.x - coords["x1"])
        h = abs(event.y - coords["y1"])
        state["text"] = canvas.create_text(
            event.x + 50, event.y + 20, text=f"{w} x {h}",
            fill="yellow", font=("Microsoft YaHei", 12, "bold"))

    def on_release(event):
        coords["x2"], coords["y2"] = event.x, event.y
        root.quit()

    def on_escape(event):
        state["cancelled"] = True
        root.quit()

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)
    root.bind("<Escape>", on_escape)
    root.mainloop()
    root.destroy()

    if state["cancelled"] or "x1" not in coords or "x2" not in coords:
        return None
    x1, y1 = coords["x1"], coords["y1"]
    x2, y2 = coords["x2"], coords["y2"]
    return min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1)


def countdown_window(seconds=4):
    """倒计时窗口：after 驱动不冻结界面，Esc 可取消"""
    result = {"cancelled": False}
    root = tk.Tk()
    root.title("准备开始")
    w, h = 340, 150
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")
    root.attributes("-topmost", True)

    label = tk.Label(root, text="", font=("Microsoft YaHei", 16, "bold"))
    label.pack(expand=True)
    tk.Label(root, text="请将鼠标放在监视区域内（Esc 取消）",
             font=("Microsoft YaHei", 10), fg="gray").pack(pady=10)

    remaining = {"n": seconds}

    def tick():
        if result["cancelled"]:
            root.quit()
            return
        if remaining["n"] > 0:
            label.config(text=f"{remaining['n']} 秒后开始监视...")
            remaining["n"] -= 1
            root.after(1000, tick)
        else:
            root.quit()

    root.bind("<Escape>", lambda e: (result.update(cancelled=True), root.quit()))
    tick()
    root.mainloop()
    root.destroy()
    return not result["cancelled"]


# ================= 模板处理 =================

def parse_action(filename, default_action):
    """文件名前缀覆盖单张模板的点击方式"""
    name = filename.lower()
    if name.startswith("match_"):
        return "match"
    if name.startswith("current_"):
        return "current"
    return default_action


def load_templates_from_folder(folder_name, default_action, logger):
    folder = os.path.join(BASE_DIR, folder_name)
    if not os.path.exists(folder):
        os.makedirs(folder)
        return []
    files = []
    for ext in ("*.png", "*.jpg", "*.jpeg"):
        files.extend(glob.glob(os.path.join(folder, ext)))

    templates = []
    for file in sorted(files):
        img = cv2.imread(file, cv2.IMREAD_GRAYSCALE)
        if img is not None:
            templates.append({
                "name": os.path.basename(file),
                "img": img,
                "action": parse_action(os.path.basename(file), default_action),
            })
        else:
            logger.warning("模板读取失败，已跳过：%s", file)

    # 大模板更可靠且更具判别力，优先匹配（命中即短路）
    templates.sort(key=lambda t: t["img"].shape[0] * t["img"].shape[1], reverse=True)
    return templates


def build_scaled_templates(templates, scales):
    """resize 一次性预缓存，主循环零重复计算"""
    out = []
    for tpl in templates:
        for s in scales:
            if abs(s - 1.0) < 1e-6:
                scaled = tpl["img"]
            else:
                w = int(tpl["img"].shape[1] * s)
                h = int(tpl["img"].shape[0] * s)
                if w < 10 or h < 10:
                    continue
                scaled = cv2.resize(tpl["img"], (w, h), interpolation=cv2.INTER_AREA)
            out.append({"name": tpl["name"], "img": scaled, "action": tpl["action"]})
    return out


# ================= 屏幕采集（mss 优先） =================

class ScreenGrabber:
    def __init__(self, region, logger):
        self.region = region
        self.logger = logger
        self.sct = mss.mss()
        self.mon = {
            "left": int(region[0]), "top": int(region[1]),
            "width": int(region[2]), "height": int(region[3]),
        }

    def grab_gray(self):
        frame = np.array(self.sct.grab(self.mon))
        return cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY)

    def close(self):
        try:
            self.sct.close()
        except Exception:
            pass


# ================= 匹配与判定 =================

def check_match(screen_gray, scaled_templates, confidence):
    best_score, best_name, best_loc, best_size, best_action = 0.0, None, None, None, None
    for tpl in scaled_templates:
        t = tpl["img"]
        th, tw = t.shape[:2]
        if th > screen_gray.shape[0] or tw > screen_gray.shape[1]:
            continue
        result = cv2.matchTemplate(screen_gray, t, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        if max_val > best_score:
            best_score, best_name = max_val, tpl["name"]
            best_loc, best_size, best_action = max_loc, (tw, th), tpl["action"]
        if max_val >= confidence:
            return True, tpl["name"], max_val, max_loc, (tw, th), tpl["action"]
    return False, best_name, best_score, best_loc, best_size, best_action


def is_mouse_in_region(region):
    mx, my = pyautogui.position()
    rx, ry, rw, rh = region
    return rx <= mx <= rx + rw and ry <= my <= ry + rh


# ================= 动作执行 =================

def perform_click(action, region, loc, size, center):
    """按模板动作点击：
       match   -> 点图标中心（使用匹配坐标）
       current -> 在鼠标当前位置按下，不移动光标
       center  -> 点监视区域中心
    """
    region_x, region_y, _, _ = region
    if action == "match" and loc is not None:
        tx = region_x + loc[0] + size[0] // 2
        ty = region_y + loc[1] + size[1] // 2
        pydirectinput.click(tx, ty)
        return f"点击图标位置 ({tx},{ty})"
    if action == "current":
        pydirectinput.click()          # 不传坐标 = 当前位置原地点击
        mx, my = pyautogui.position()
        return f"原地点击 ({mx},{my})"
    cx, cy = center
    pydirectinput.click(cx, cy)
    return f"点击区域中心 ({cx},{cy})"


# ================= 监视主循环（工作线程） =================

def monitor_loop(logger, cfg, region, f_scaled, left_scaled):
    grabber = ScreenGrabber(region, logger)
    region_x, region_y, region_w, region_h = region
    center = (region_x + region_w // 2, region_y + region_h // 2)
    require_in_region = cfg["REQUIRE_MOUSE_IN_REGION"] != 0
    press_key = str(cfg["PRESS_KEY"]).strip().lower()

    pydirectinput.PAUSE = 0.05
    pyautogui.FAILSAFE = True

    last_action_time = 0.0
    last_silent_log = 0.0

    def silent_block(name, score, now):
        if now - last_silent_log > 3:
            logger.info("[静默] 匹配 %s (%.2f)，鼠标不在选区，已拦截", name, score)

    try:
        while STATE["running"]:
            try:
                if STATE["paused"]:
                    time.sleep(0.3)
                    continue

                screen_gray = grabber.grab_gray()
                now = time.time()

                if now - last_action_time < cfg["COOLDOWN"]:
                    time.sleep(cfg["CHECK_INTERVAL"])
                    continue

                hit, name, score, loc, size, action = check_match(
                    screen_gray, left_scaled, cfg["CONFIDENCE"])

                if hit:
                    if require_in_region and not is_mouse_in_region(region):
                        silent_block(name, score, now)
                        last_silent_log = now
                        time.sleep(cfg["CHECK_INTERVAL"])
                        continue        # 拦截后不再检查 F 模板

                    detail = perform_click(action, region, loc, size, center)
                    logger.info("匹配 %s (%.2f) -> %s [左键/%s]",
                                name, score, detail, action)
                    last_action_time = time.time()
                    time.sleep(cfg["ACTION_DELAY"])
                    continue

                hit, name, score, loc, size, action = check_match(
                    screen_gray, f_scaled, cfg["CONFIDENCE"])

                if hit:
                    if require_in_region and not is_mouse_in_region(region):
                        silent_block(name, score, now)
                        last_silent_log = now
                        time.sleep(cfg["CHECK_INTERVAL"])
                        continue

                    pydirectinput.press(press_key)
                    logger.info("匹配 %s (%.2f) -> 按下 %s 键",
                                name, score, press_key.upper())
                    last_action_time = time.time()
                    time.sleep(cfg["ACTION_DELAY"])
                    continue

                if cfg["HEARTBEAT"] != 0:
                    console_print(f"\r[心跳] 左键最高: {score:.2f}    ", end="")

            except pyautogui.FailSafeException:
                logger.warning("触发 FailSafe（鼠标移到屏幕角落），程序停止。")
                STATE["running"] = False
                break
            except Exception as e:
                logger.warning("发生异常：%s", e)
                time.sleep(2)

            time.sleep(cfg["CHECK_INTERVAL"])
    finally:
        grabber.close()
        console_print("")
        logger.info("监视循环已结束。")


# ================= 主流程 =================

def main():
    enable_dpi_awareness()              # 必须最先执行
    logger = setup_logging()

    if ensure_single_instance() is None:
        show_error("启动失败", "自动剧情工具已在运行中，请勿重复启动。")
        return

    cfg = load_config(logger)
    logger.info("配置加载完成：CONFIDENCE=%.2f CHECK_INTERVAL=%.2f COOLDOWN=%.2f CLICK_MODE=%s",
                cfg["CONFIDENCE"], cfg["CHECK_INTERVAL"], cfg["COOLDOWN"], cfg["CLICK_MODE"])

    if not show_startup_dialog(cfg):
        return

    f_templates = load_templates_from_folder("F_Picture", "press", logger)
    left_templates = load_templates_from_folder("Left_Picture", str(cfg["CLICK_MODE"]).lower(), logger)
    if not f_templates and not left_templates:
        show_error("错误", "未找到任何目标图片！\n\n请在 F_Picture 或 Left_Picture 文件夹中放入截图。")
        return

    scales = cfg["SCALES"] if cfg["SCALES"] else [1.0]
    f_scaled = build_scaled_templates(f_templates, scales)
    left_scaled = build_scaled_templates(left_templates, scales)
    logger.info("模板加载完成：F %d 实例 / 左键 %d 实例 / 尺度 %s",
                len(f_scaled), len(left_scaled), scales)

    region = select_region()
    if region is None or region[2] < 10 or region[3] < 10:
        logger.info("未选择有效监视区域，退出。")
        return
    logger.info("监视区域已设定: %s", region)

    try:
        keyboard.add_hotkey(cfg["HOTKEY_PAUSE"], toggle_pause)
        keyboard.add_hotkey(cfg["HOTKEY_EXIT"], request_exit)
    except Exception as e:
        logger.warning("热键注册失败（%s），回退默认 alt+p / alt+q：%s", e, "")
        keyboard.add_hotkey("alt+p", toggle_pause)
        keyboard.add_hotkey("alt+q", request_exit)

    if not countdown_window(4) or not STATE["running"]:
        logger.info("启动被取消，退出。")
        keyboard.unhook_all()
        return

    logger.info("监视已启动。快捷键：%s 暂停 / %s 退出。鼠标移出选区将自动静默。",
                cfg["HOTKEY_PAUSE"].upper(), cfg["HOTKEY_EXIT"].upper())
    notify("自动剧情工具", "监视已启动")

    worker = threading.Thread(
        target=monitor_loop, args=(logger, cfg, region, f_scaled, left_scaled),
        daemon=True)
    worker.start()

    # 主线程跑 Tk：负责气泡通知与退出收尾
    root = tk.Tk()
    root.withdraw()

    def pump():
        try:
            while True:
                title, message = NOTIFY_QUEUE.get_nowait()
                if title == "__QUIT__":
                    # 收到延时退出信号，关闭主窗口
                    root.quit()
                    return
                _show_notification(root, title, message)
        except Empty:
            pass
        #if not STATE["running"] and not worker.is_alive():
        #    root.quit()
        #   return
        root.after(150, pump)

    pump()
    root.mainloop()
    root.destroy()

    keyboard.unhook_all()
    logger.info("程序已退出。")


if __name__ == "__main__":
    main()