import json
import glob
import os
import shutil
import subprocess
import sys

CONFIG_FILE = "deepseek_config.json"

class LogEntry:
    def __init__(self, brief, detail=None, level="info",
                 group=None, index=None, image_path=None, file_path=None):
        self.brief = brief
        self.detail = detail or brief
        self.level = level
        self.group = group
        self.index = index
        self.image_path = image_path
        self.file_path = file_path

def load_config():
    default = {
        "excel_path": "ceshi.xlsx",
        "output_path": "results.xlsx",
        "prefix": "",
        "mode": "expert",
        "deep_think": True,
        "smart_search": True,
        "enable_context": True,
        "context_col": "",
        "question_col": "",
        "context_label": "历史对话",
        "question_label": "问题",
        "browser_mode": "maximized",
        "browser_width": 1920,
        "browser_height": 1080,
        "browser_x": 0,
        "browser_y": 0,
        "skip_login": False,
        "enable_skip": False,
        "skip_col": "",
        "skip_values": "跳过",
        "skip_mark": "SKIPPED",
        "font_family": "微软雅黑",
        "font_size": 12,
        "window_geometry": "1200x750",
        "window_maximized": False,
        "preset_prefixes": {
            "默认指令1": "请用简洁、专业的中文回答。",
            "默认指令2": "请提供详细的步骤和代码示例。",
            "默认指令3": "请扮演一名资深专家，给出深入分析。"
        }
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                default.update(saved)
        except:
            pass
    return default

def save_config(config):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
    except:
        pass

def clean_old_logs(max_size_mb=20):
    log_dirs = glob.glob("logs_*")
    if not log_dirs:
        return
    log_dirs.sort(key=lambda d: os.path.getmtime(d) if os.path.exists(d) else 0)
    total = 0
    for d in log_dirs[:]:
        size = sum(os.path.getsize(os.path.join(d, f))
                   for f in os.listdir(d) if os.path.isfile(os.path.join(d, f)))
        total += size
        if total > max_size_mb * 1024 * 1024:
            try:
                shutil.rmtree(d)
            except:
                pass
        else:
            break

def kill_chrome_processes():
    try:
        if sys.platform == "win32":
            subprocess.run("taskkill /F /IM chrome.exe", shell=True, capture_output=True)
        else:
            subprocess.run("pkill -f chrome", shell=True, capture_output=True)
        return True
    except:
        return False