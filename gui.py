import customtkinter as ctk
from tkinter import filedialog, messagebox, simpledialog, Toplevel
import time, datetime, queue, threading, os, pandas as pd, subprocess, sys
from PIL import Image, ImageTk

from utils import load_config, save_config, clean_old_logs, kill_chrome_processes, LogEntry
from worker import WorkerThread


class DeepSeekGUI(ctk.CTk):

    def __init__(self):
        super().__init__()
        self.title("DeepSeek 批量提问助手 v2.0")
        self.geometry("1200x750")
        self.minsize(1000, 650)

        self.config = load_config()

        # 恢复窗口几何
        win_geom = self.config.get("window_geometry", "1200x750")
        if win_geom:
            self.geometry(win_geom)
        if self.config.get("window_maximized", False):
            self.state('zoomed')

        # 加载预设前缀指令
        self.preset_prefixes = self.config.get("preset_prefixes", {
            "默认指令1": "请用简洁、专业的中文回答。",
            "默认指令2": "请提供详细的步骤和代码示例。",
            "默认指令3": "请扮演一名资深专家，给出深入分析。"
        })
        self.preset_names = list(self.preset_prefixes.keys())

        # 恢复筛选配置
        self.active_filters = self.config.get("active_filters", ["处理进度", "模型回答", "系统报错", "普通信息"])

        # 固定日志根目录
        script_dir = os.path.dirname(os.path.abspath(__file__))
        self.script_dir = script_dir
        self.base_log_dir = os.path.join(script_dir, "logs")
        os.makedirs(self.base_log_dir, exist_ok=True)
        self.current_log_dir = None

        clean_old_logs()

        # ---------- 全局状态栏 ----------
        self.status_bar = ctk.CTkFrame(self, height=60, corner_radius=10)
        self.status_bar.grid(row=0, column=0, padx=10, pady=10, sticky="ew")
        self.status_bar.grid_propagate(False)

        self.progressbar = ctk.CTkProgressBar(self.status_bar, width=300, height=30)
        self.progressbar.set(0)
        self.progressbar.grid(row=0, column=0, padx=10, pady=10)

        self.progress_label = ctk.CTkLabel(self.status_bar, text="0/0 (0%)", width=100, height=30)
        self.progress_label.grid(row=0, column=1, padx=5)

        self.time_label = ctk.CTkLabel(self.status_bar, text="已用: -- | 预计: -- | 剩余: --", width=300, height=30, font=("", 14, "bold"))
        self.time_label.grid(row=0, column=2, padx=10)

        self.status_title = ctk.CTkLabel(self.status_bar, text="状态：就绪", font=("", 16, "bold"), text_color="gray")
        self.status_title.grid(row=0, column=3, padx=20, sticky="e")
        self.status_bar.columnconfigure(3, weight=1)

        # ---------- 主内容区 ----------
        main_frame = ctk.CTkFrame(self)
        main_frame.grid(row=1, column=0, padx=10, pady=5, sticky="nsew")
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        main_frame.grid_columnconfigure(0, minsize=450)
        main_frame.grid_columnconfigure(1, weight=1)
        main_frame.grid_rowconfigure(0, weight=1)

        # 左侧标签页
        self.tabview = ctk.CTkTabview(main_frame, width=450)
        self.tabview.grid(row=0, column=0, padx=5, pady=5, sticky="nsew")
        self.tabview.add("基础配置")
        self.tabview.add("模型设置")
        self.tabview.add("高级功能")
        self.tabview.add("浏览器配置")
        self.build_basic_tab()
        self.build_model_tab()
        self.build_advanced_tab()
        self.build_browser_tab()

        # ---------- 右侧监控面板 ----------
        monitor_frame = ctk.CTkFrame(main_frame)
        monitor_frame.grid(row=0, column=1, padx=5, pady=5, sticky="nsew")
        monitor_frame.grid_rowconfigure(0, weight=0)
        monitor_frame.grid_rowconfigure(1, weight=1)
        monitor_frame.grid_rowconfigure(2, weight=1)
        monitor_frame.grid_columnconfigure(0, weight=1)
        monitor_frame.grid_columnconfigure(1, weight=2)

        # 控制栏
        control_bar = ctk.CTkFrame(monitor_frame, fg_color="transparent")
        control_bar.grid(row=0, column=0, columnspan=2, padx=5, pady=5, sticky="ew")
        control_bar.grid_columnconfigure(0, weight=0)
        control_bar.grid_columnconfigure(1, weight=1)
        control_bar.grid_columnconfigure(2, weight=0)

        self.auto_scroll_var = ctk.BooleanVar(value=True)
        auto_scroll_cb = ctk.CTkCheckBox(control_bar, text="自动滚动", variable=self.auto_scroll_var)
        auto_scroll_cb.grid(row=0, column=0, padx=5, sticky="w")

        self.filter_btn = ctk.CTkButton(control_bar, text="筛选 ▼", width=80, command=self._show_filter_dialog)
        self.filter_btn.grid(row=0, column=2, padx=5, sticky="e")

        # 左侧：日志 + 信息面板
        left_frame = ctk.CTkFrame(monitor_frame, fg_color="transparent")
        left_frame.grid(row=1, column=0, sticky="nsew")
        left_frame.grid_rowconfigure(0, weight=1)
        left_frame.grid_rowconfigure(1, weight=0)
        left_frame.grid_columnconfigure(0, weight=1)

        self.log_list = ctk.CTkScrollableFrame(left_frame, label_text="日志", width=300)
        self.log_list.grid(row=0, column=0, padx=5, pady=5, sticky="nsew")

        info_frame = ctk.CTkFrame(left_frame, fg_color=("gray85", "gray25"), corner_radius=8)
        info_frame.grid(row=1, column=0, padx=5, pady=5, sticky="ew")
        info_frame.grid_columnconfigure(1, weight=1)

        row_info = 0
        ctk.CTkLabel(info_frame, text="📂 输入文件：", font=("", 11)).grid(row=row_info, column=0, padx=5, pady=2, sticky="w")
        self.input_path_label = ctk.CTkLabel(info_frame, text="未选择", anchor="w", text_color="blue", cursor="hand2")
        self.input_path_label.grid(row=row_info, column=1, padx=5, pady=2, sticky="ew")
        self.input_path_label.bind("<Button-1>", lambda e: self._open_folder(self.file_var.get()))
        row_info += 1

        ctk.CTkLabel(info_frame, text="💾 输出文件：", font=("", 11)).grid(row=row_info, column=0, padx=5, pady=2, sticky="w")
        self.output_path_label = ctk.CTkLabel(info_frame, text="未选择", anchor="w", text_color="blue", cursor="hand2")
        self.output_path_label.grid(row=row_info, column=1, padx=5, pady=2, sticky="ew")
        self.output_path_label.bind("<Button-1>", lambda e: self._open_folder(self.out_var.get()))
        row_info += 1

        ctk.CTkLabel(info_frame, text="📁 日志截图目录：", font=("", 11)).grid(row=row_info, column=0, padx=5, pady=2, sticky="w")
        self.logdir_label = ctk.CTkLabel(info_frame, text="未运行", anchor="w", text_color="blue", cursor="hand2")
        self.logdir_label.grid(row=row_info, column=1, padx=5, pady=2, sticky="ew")
        self.logdir_label.bind("<Button-1>", lambda e: self._open_folder(self.current_log_dir))
        row_info += 1

        # 右侧上半：文本详情
        detail_frame = ctk.CTkFrame(monitor_frame)
        detail_frame.grid(row=1, column=1, padx=5, pady=5, sticky="nsew")
        detail_frame.grid_rowconfigure(0, weight=1)
        detail_frame.grid_columnconfigure(0, weight=1)
        self.detail_text = ctk.CTkTextbox(detail_frame, wrap="word", state="disabled", height=150)
        self.detail_text.grid(row=0, column=0, padx=5, pady=5, sticky="nsew")

        # 右侧下半：图片历史
        self.image_scroll_frame = ctk.CTkScrollableFrame(monitor_frame, label_text="截图历史")
        self.image_scroll_frame.grid(row=2, column=1, padx=5, pady=5, sticky="nsew")
        self.image_scroll_frame.grid_columnconfigure(0, weight=1)

        clear_btn = ctk.CTkButton(self.image_scroll_frame, text="清空所有图片", width=100, command=self._clear_images)
        clear_btn.pack(pady=5)

        # 底部操作栏
        btn_bar = ctk.CTkFrame(self, fg_color="transparent")
        btn_bar.grid(row=2, column=0, padx=10, pady=10, sticky="ew")
        btn_bar.grid_columnconfigure((0,1,2,3), weight=1)

        self.start_btn = ctk.CTkButton(btn_bar, text="🚀 开始执行", fg_color="#2e86c1", command=self.start_execution, height=40)
        self.start_btn.grid(row=0, column=0, padx=10, sticky="ew")
        self.stop_btn = ctk.CTkButton(btn_bar, text="⏹ 停止执行", fg_color="#e74c3c", command=self.stop_execution, state="disabled", height=40)
        self.stop_btn.grid(row=0, column=1, padx=10, sticky="ew")
        self.login_btn = ctk.CTkButton(btn_bar, text="✅ 确认已登录", fg_color="#27ae60", command=self.confirm_login, state="disabled", height=40)
        self.login_btn.grid(row=0, column=2, padx=10, sticky="ew")
        self.kill_btn = ctk.CTkButton(btn_bar, text="🔧 清理 Chrome 进程", fg_color="#e67e22", command=self.kill_chrome, height=40)
        self.kill_btn.grid(row=0, column=3, padx=10, sticky="ew")

        # 内部变量
        self.log_entries = []
        self.log_widgets = []
        self.start_time = None
        self.processed_items = 0
        self.total_items = 0

        self.stop_event = threading.Event()
        self.login_confirmed = threading.Event()
        self.log_queue = queue.Queue()
        self.worker = None

        self.after(100, self.process_queue)
        self.protocol("WM_DELETE_WINDOW", self.on_closing)

        # 联动
        self.toggle_context()
        self.toggle_skip()
        self.on_browser_mode_change()

        # 初始化信息面板
        self._update_info_panel()
        self.file_var.trace_add("write", lambda *a: self._update_info_panel())
        self.out_var.trace_add("write", lambda *a: self._update_info_panel())

    # ---------- 信息面板辅助 ----------
    def _update_info_panel(self):
        in_path = self.file_var.get()
        out_path = self.out_var.get()
        self.input_path_label.configure(text=in_path if in_path else "未选择")
        self.output_path_label.configure(text=out_path if out_path else "未选择")
        if self.current_log_dir and os.path.exists(self.current_log_dir):
            self.logdir_label.configure(text=self.current_log_dir)
        else:
            self.logdir_label.configure(text="未运行")

    def _open_folder(self, path):
        if not path or path == "未选择" or path == "未运行":
            messagebox.showwarning("提示", "路径未设置")
            return
        if not os.path.isabs(path):
            abs_path = os.path.join(self.script_dir, path)
        else:
            abs_path = path
        if os.path.isfile(abs_path):
            folder = os.path.dirname(abs_path)
        else:
            folder = abs_path
        if not folder or not os.path.exists(folder):
            folder = self.script_dir
        if sys.platform == "win32":
            os.startfile(folder)
        elif sys.platform == "darwin":
            subprocess.run(["open", folder])
        else:
            subprocess.run(["xdg-open", folder])

    # ---------- 日志筛选 ----------
    def _show_filter_dialog(self):
        dialog = ctk.CTkToplevel(self)
        dialog.title("筛选日志")
        dialog.geometry("250x280")
        dialog.resizable(False, False)
        dialog.grab_set()
        dialog.update_idletasks()
        x = (self.winfo_x() + self.winfo_width() // 2) - 125
        y = (self.winfo_y() + self.winfo_height() // 2) - 140
        dialog.geometry(f"+{x}+{y}")

        options = ["处理进度", "模型回答", "系统报错", "普通信息"]
        vars_dict = {}
        for opt in options:
            var = ctk.BooleanVar(value=opt in self.active_filters)
            cb = ctk.CTkCheckBox(dialog, text=opt, variable=var)
            cb.pack(anchor="w", padx=20, pady=5)
            vars_dict[opt] = var

        def apply():
            self.active_filters = [opt for opt, var in vars_dict.items() if var.get()]
            self.config["active_filters"] = self.active_filters
            save_config(self.config)
            self._refresh_log_display()
            dialog.destroy()

        ctk.CTkButton(dialog, text="确定", command=apply).pack(pady=10)

    def _should_show_log(self, entry):
        if not self.active_filters:
            return True
        if "处理进度" in self.active_filters and entry.group == "progress_start":
            return True
        if "模型回答" in self.active_filters and entry.brief == "模型回答":
            return True
        if "系统报错" in self.active_filters and entry.level == "error":
            return True
        if "普通信息" in self.active_filters:
            if entry.group != "progress_start" and entry.brief != "模型回答" and entry.level != "error":
                return True
        return False

    def _refresh_log_display(self):
        for w in self.log_widgets:
            w.destroy()
        self.log_widgets.clear()
        for entry in self.log_entries:
            if self._should_show_log(entry):
                self._add_log_widget(entry)
        if hasattr(self.log_list, '_parent_canvas'):
            self.log_list._parent_canvas.configure(scrollregion=self.log_list._parent_canvas.bbox("all"))
        if self.auto_scroll_var.get():
            self.after(100, self._scroll_log_bottom)

    def _add_log_widget(self, entry):
        LEVEL_COLORS = {
            "info": "#3498db",
            "progress": "#1abc9c",
            "warn": "#f39c12",
            "error": "#e74c3c",
            "special": "#2ecc71",
        }
        color = LEVEL_COLORS.get(entry.level, "gray")
        brief = entry.brief[:100]
        btn = ctk.CTkButton(self.log_list, text=brief, anchor="w",
                            fg_color=color, text_color="white" if ctk.get_appearance_mode() != "Dark" else "black",
                            hover_color="gray50",
                            command=lambda e=entry: self.show_detail(e))
        btn.pack(fill="x", padx=5, pady=2)
        self.log_widgets.append(btn)
        if hasattr(self.log_list, '_parent_canvas'):
            self.log_list._parent_canvas.configure(scrollregion=self.log_list._parent_canvas.bbox("all"))
        if self.auto_scroll_var.get():
            self.after(50, self._scroll_log_bottom)

    def _scroll_log_bottom(self):
        if hasattr(self.log_list, '_parent_canvas'):
            self.log_list._parent_canvas.yview_moveto(1.0)

    # ---------- 图片历史 ----------
    def _add_image_to_history(self, entry):
        if not os.path.exists(entry.image_path):
            return
        frame = ctk.CTkFrame(self.image_scroll_frame, fg_color="transparent")
        frame.pack(fill="x", padx=5, pady=5)

        title = entry.brief if entry.brief else "截图"
        if entry.index is not None:
            title = f"[第{entry.index + 1}行] {title}"
        lbl = ctk.CTkLabel(frame, text=title, anchor="w", font=("", 12, "bold"))
        lbl.pack(anchor="w", padx=5)

        try:
            img = Image.open(entry.image_path)
            img.thumbnail((300, 200))
            photo = ImageTk.PhotoImage(img)
            img_label = ctk.CTkLabel(frame, image=photo, text="")
            img_label.image = photo
            img_label.pack(pady=5)
            img_label.bind("<Double-Button-1>", lambda e, path=entry.image_path: self.view_image_big(path))
        except Exception as e:
            err_lbl = ctk.CTkLabel(frame, text=f"图片加载失败: {e}", text_color="red")
            err_lbl.pack(pady=5)

        def del_this():
            frame.destroy()
        del_btn = ctk.CTkButton(frame, text="✖", width=30, height=30, fg_color="gray", hover_color="red", command=del_this)
        del_btn.pack(anchor="ne", padx=5)

        self.image_scroll_frame._parent_canvas.yview_moveto(1.0)

    def _clear_images(self):
        children = list(self.image_scroll_frame.winfo_children())
        for child in children:
            if child != children[0]:
                child.destroy()
        self.add_log(LogEntry("已清空截图历史", level="info"))

    # ---------- 左侧标签页 ----------
    def build_basic_tab(self):
        tab = self.tabview.tab("基础配置")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_columnconfigure(1, weight=2)
        row = 0

        ctk.CTkLabel(tab, text="Excel 文件:").grid(row=row, column=0, padx=5, sticky="w")
        self.file_var = ctk.StringVar(value=self.config.get("excel_path", "ceshi.xlsx"))
        ctk.CTkEntry(tab, textvariable=self.file_var).grid(row=row, column=1, padx=5, pady=5, sticky="ew")
        ctk.CTkButton(tab, text="浏览", width=60, command=self.browse_file).grid(row=row, column=2, padx=5)
        row += 1

        ctk.CTkLabel(tab, text="输出文件:").grid(row=row, column=0, padx=5, sticky="w")
        self.out_var = ctk.StringVar(value=self.config.get("output_path", "results.xlsx"))
        ctk.CTkEntry(tab, textvariable=self.out_var).grid(row=row, column=1, padx=5, pady=5, sticky="ew")
        ctk.CTkButton(tab, text="浏览", width=60, command=self.browse_output).grid(row=row, column=2, padx=5)
        row += 1

        ctk.CTkButton(tab, text="🔄 加载列名", command=self.load_columns).grid(row=row, column=1, padx=5, pady=5, sticky="w")
        row += 1

        self.enable_context = ctk.BooleanVar(value=self.config.get("enable_context", True))
        self.context_cb = ctk.CTkCheckBox(tab, text="启用上下文（双列模式）", variable=self.enable_context, command=self.toggle_context)
        self.context_cb.grid(row=row, column=0, columnspan=2, padx=5, sticky="w")
        row += 1

        ctk.CTkLabel(tab, text="上下文列:").grid(row=row, column=0, padx=5, sticky="w")
        self.context_col_var = ctk.StringVar(value=self.config.get("context_col", ""))
        self.context_menu = ctk.CTkOptionMenu(tab, variable=self.context_col_var, values=["请先加载列名"])
        self.context_menu.grid(row=row, column=1, padx=5, pady=5, sticky="ew")
        self.context_label_var = ctk.StringVar(value=self.config.get("context_label", "历史对话"))
        ctk.CTkEntry(tab, textvariable=self.context_label_var, width=80).grid(row=row, column=2, padx=5)
        row += 1

        ctk.CTkLabel(tab, text="问题列:").grid(row=row, column=0, padx=5, sticky="w")
        self.ques_col_var = ctk.StringVar(value=self.config.get("question_col", ""))
        self.ques_menu = ctk.CTkOptionMenu(tab, variable=self.ques_col_var, values=["请先加载列名"])
        self.ques_menu.grid(row=row, column=1, padx=5, pady=5, sticky="ew")
        self.ques_label_var = ctk.StringVar(value=self.config.get("question_label", "问题"))
        ctk.CTkEntry(tab, textvariable=self.ques_label_var, width=80).grid(row=row, column=2, padx=5)
        row += 1

        ctk.CTkLabel(tab, text="前缀指令:").grid(row=row, column=0, padx=5, sticky="nw")
        self.prefix_box = ctk.CTkTextbox(tab, height=80)
        self.prefix_box.grid(row=row, column=1, columnspan=2, padx=5, pady=5, sticky="ew")
        self.prefix_box.insert("1.0", self.config.get("prefix", ""))
        row += 1

        # 常用指令预设面板
        preset_frame = ctk.CTkFrame(tab, fg_color="transparent")
        preset_frame.grid(row=row, column=0, columnspan=3, padx=5, pady=5, sticky="ew")
        preset_frame.grid_columnconfigure(0, weight=0)
        preset_frame.grid_columnconfigure(1, weight=1)
        row_in_preset = 0

        ctk.CTkLabel(preset_frame, text="常用指令:").grid(row=row_in_preset, column=0, padx=5, pady=2, sticky="w")
        self.preset_var = ctk.StringVar()
        if self.preset_names:
            self.preset_var.set(self.preset_names[0])
        self.preset_menu = ctk.CTkOptionMenu(preset_frame, variable=self.preset_var, values=self.preset_names, width=200, command=self._on_preset_selected)
        self.preset_menu.grid(row=row_in_preset, column=1, padx=5, pady=2, sticky="ew")
        row_in_preset += 1

        btn_frame = ctk.CTkFrame(preset_frame, fg_color="transparent")
        btn_frame.grid(row=row_in_preset, column=0, columnspan=2, pady=5, sticky="ew")
        btn_frame.grid_columnconfigure((0,1,2,3), weight=1)

        ctk.CTkButton(btn_frame, text="插入", width=80, command=self._insert_preset_prefix).grid(row=0, column=0, padx=5)
        ctk.CTkButton(btn_frame, text="保存当前", width=100, command=self._save_current_prefix_as_preset).grid(row=0, column=1, padx=5)
        ctk.CTkButton(btn_frame, text="编辑", width=80, command=self._edit_selected_preset).grid(row=0, column=2, padx=5)
        ctk.CTkButton(btn_frame, text="清空", width=80, fg_color="#d32f2f", hover_color="#b71c1c", command=self._clear_prefix_box).grid(row=0, column=3, padx=5)

    def build_model_tab(self):
        tab = self.tabview.tab("模型设置")
        tab.grid_columnconfigure(0, weight=1)

        # 模型平台选择
        ctk.CTkLabel(tab, text="模型平台:", anchor="w").pack(pady=5, padx=10, fill="x")
        self.platform_var = ctk.StringVar(value=self.config.get("model_platform", "DeepSeek"))
        platform_menu = ctk.CTkOptionMenu(tab, variable=self.platform_var, values=["DeepSeek", "豆包"], width=150,
                                          command=self._on_platform_changed)
        platform_menu.pack(pady=5)

        # 用于存放动态控件的容器
        self.model_specific_frame = ctk.CTkFrame(tab, fg_color="transparent")
        self.model_specific_frame.pack(fill="x", pady=10)

        # 初始化当前平台的控件
        self._on_platform_changed(self.platform_var.get())

    def _on_platform_changed(self, platform):
        """根据平台动态显示不同的模型设置控件"""
        # 清空原有控件
        for widget in self.model_specific_frame.winfo_children():
            widget.destroy()

        if platform == "DeepSeek":
            # DeepSeek 的快速/专家模式 + 深度思考/智能搜索
            ctk.CTkLabel(self.model_specific_frame, text="模型模式 (快速更短，专家更详细)").pack(pady=5)
            self.mode_var = ctk.StringVar(value=self.config.get("mode", "expert"))
            mode_frame = ctk.CTkFrame(self.model_specific_frame, fg_color="transparent")
            mode_frame.pack(pady=5)
            fast_radio = ctk.CTkRadioButton(mode_frame, text="快速模式", variable=self.mode_var, value="fast",
                                            fg_color="#2e86c1", hover_color="#1a5a8a")
            fast_radio.pack(side="left", padx=10)
            expert_radio = ctk.CTkRadioButton(mode_frame, text="专家模式", variable=self.mode_var, value="expert",
                                              fg_color="#27ae60", hover_color="#1e7a4a")
            expert_radio.pack(side="left", padx=10)

            self.deep_var = ctk.BooleanVar(value=self.config.get("deep_think", True))
            self.search_var = ctk.BooleanVar(value=self.config.get("smart_search", True))
            ctk.CTkCheckBox(self.model_specific_frame, text="深度思考", variable=self.deep_var).pack(pady=5)
            ctk.CTkCheckBox(self.model_specific_frame, text="智能搜索", variable=self.search_var).pack(pady=5)

        elif platform == "豆包":
            # 豆包的模式：快速 / 思考 / 专家
            ctk.CTkLabel(self.model_specific_frame, text="豆包模式:", anchor="w").pack(pady=5, fill="x")
            self.doubao_mode_var = ctk.StringVar(value=self.config.get("doubao_mode", "快速"))
            mode_menu = ctk.CTkOptionMenu(self.model_specific_frame, variable=self.doubao_mode_var,
                                          values=["快速", "思考", "专家"])
            mode_menu.pack(pady=5)

    def build_advanced_tab(self):
        tab = self.tabview.tab("高级功能")
        tab.grid_columnconfigure(0, weight=1)
        row = 0

        self.enable_skip = ctk.BooleanVar(value=self.config.get("enable_skip", False))
        ctk.CTkCheckBox(tab, text="启用跳过规则", variable=self.enable_skip, command=self._on_enable_skip_changed).grid(row=row, column=0, padx=5, sticky="w")
        row += 1

        ctk.CTkLabel(tab, text="跳过列:").grid(row=row, column=0, padx=5, sticky="w")
        self.skip_col_var = ctk.StringVar(value=self.config.get("skip_col", ""))
        self.skip_menu = ctk.CTkOptionMenu(tab, variable=self.skip_col_var, values=["请先加载列名"])
        self.skip_menu.grid(row=row, column=1, padx=5, pady=5, sticky="ew")
        row += 1

        ctk.CTkLabel(tab, text="跳过值 (逗号分隔):").grid(row=row, column=0, padx=5, sticky="w")
        self.skip_values_var = ctk.StringVar(value=self.config.get("skip_values", "跳过"))
        self.skip_values_entry = ctk.CTkEntry(tab, textvariable=self.skip_values_var)
        self.skip_values_entry.grid(row=row, column=1, padx=5, pady=5, sticky="ew")
        row += 1

        ctk.CTkLabel(tab, text="跳过标记:").grid(row=row, column=0, padx=5, sticky="w")
        self.skip_mark_var = ctk.StringVar(value=self.config.get("skip_mark", "SKIPPED"))
        ctk.CTkEntry(tab, textvariable=self.skip_mark_var).grid(row=row, column=1, padx=5, pady=5, sticky="ew")
        row += 1

        self.skip_login_var = ctk.BooleanVar(value=self.config.get("skip_login", False))
        ctk.CTkCheckBox(tab, text="跳过登录（直接执行）", variable=self.skip_login_var).grid(row=row, column=0, columnspan=2, padx=5, sticky="w")

    def build_browser_tab(self):
        tab = self.tabview.tab("浏览器配置")
        tab.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(tab, text="窗口模式:").pack(pady=5)
        self.browser_mode_var = ctk.StringVar(value=self.config.get("browser_mode", "maximized"))
        self.browser_menu = ctk.CTkOptionMenu(tab, variable=self.browser_mode_var, values=["maximized", "minimized", "custom"], command=self.on_browser_mode_change)
        self.browser_menu.pack(pady=5)

        self.custom_frame = ctk.CTkFrame(tab, fg_color="transparent")
        self.custom_frame.pack(pady=5)
        self.width_var = ctk.StringVar(value=str(self.config.get("browser_width", 1920)))
        self.height_var = ctk.StringVar(value=str(self.config.get("browser_height", 1080)))
        self.x_var = ctk.StringVar(value=str(self.config.get("browser_x", 0)))
        self.y_var = ctk.StringVar(value=str(self.config.get("browser_y", 0)))
        ctk.CTkLabel(self.custom_frame, text="宽:").pack(side="left", padx=2)
        self.w_entry = ctk.CTkEntry(self.custom_frame, textvariable=self.width_var, width=60)
        self.w_entry.pack(side="left", padx=2)
        ctk.CTkLabel(self.custom_frame, text="高:").pack(side="left", padx=2)
        self.h_entry = ctk.CTkEntry(self.custom_frame, textvariable=self.height_var, width=60)
        self.h_entry.pack(side="left", padx=2)
        ctk.CTkLabel(self.custom_frame, text="X:").pack(side="left", padx=2)
        self.x_entry = ctk.CTkEntry(self.custom_frame, textvariable=self.x_var, width=60)
        self.x_entry.pack(side="left", padx=2)
        ctk.CTkLabel(self.custom_frame, text="Y:").pack(side="left", padx=2)
        self.y_entry = ctk.CTkEntry(self.custom_frame, textvariable=self.y_var, width=60)
        self.y_entry.pack(side="left", padx=2)
        self.on_browser_mode_change()

    # ---------- 联动 ----------
    def toggle_context(self):
        state = "normal" if self.enable_context.get() else "disabled"
        self.context_menu.configure(state=state)

    def toggle_skip(self):
        state = "normal" if self.enable_skip.get() else "disabled"
        self.skip_menu.configure(state=state)
        self.skip_values_entry.configure(state=state)

    def on_browser_mode_change(self, *args):
        state = "normal" if self.browser_mode_var.get() == "custom" else "disabled"
        for child in self.custom_frame.winfo_children():
            child.configure(state=state)

    # ---------- 文件选择 ----------
    def browse_file(self):
        path = filedialog.askopenfilename(filetypes=[("Excel", "*.xlsx *.xls")])
        if path:
            self.file_var.set(path)

    def browse_output(self):
        path = filedialog.asksaveasfilename(defaultextension=".xlsx", filetypes=[("Excel", "*.xlsx")])
        if path:
            self.out_var.set(path)

    def load_columns(self):
        file = self.file_var.get()
        if not file:
            messagebox.showerror("错误", "请先选择 Excel 文件")
            return
        try:
            df = pd.read_excel(file, sheet_name=0, nrows=0)
            cols = list(df.columns)
            if not cols:
                raise ValueError("表头为空")
            self.context_menu.configure(values=cols)
            self.ques_menu.configure(values=cols)
            self.skip_menu.configure(values=cols)
            if cols:
                if self.context_col_var.get() not in cols:
                    self.context_col_var.set(cols[0])
                if self.ques_col_var.get() not in cols:
                    self.ques_col_var.set(cols[1] if len(cols) > 1 else cols[0])
                if self.skip_col_var.get() not in cols:
                    self.skip_col_var.set(cols[0] if cols else "")
            self.add_log(LogEntry(f"已加载列名：{', '.join(cols)}"))
        except Exception as e:
            messagebox.showerror("加载列名失败", str(e))

    # ---------- 日志处理 ----------
    def add_log(self, entry):
        if entry.brief == "WAIT_LOGIN":
            self.show_login_alert()
            return
        if entry.brief == "FINISHED":
            self.finish_task()
            return
        if entry.group == "progress_update":
            self.processed_items = entry.index
            self.update_progress_display()
            return
        self.log_entries.append(entry)
        if self._should_show_log(entry):
            self._add_log_widget(entry)
        if entry.image_path and os.path.exists(entry.image_path):
            self._add_image_to_history(entry)

    def show_detail(self, entry):
        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0", "end")
        self.detail_text.insert("end", entry.detail)
        self.detail_text.configure(state="disabled")

    def view_image_big(self, img_path):
        top = Toplevel(self)
        top.title("图片查看 - 双击关闭")
        img = Image.open(img_path)
        img.thumbnail((self.winfo_screenwidth() * 0.8, self.winfo_screenheight() * 0.8))
        photo = ImageTk.PhotoImage(img)
        label = ctk.CTkLabel(top, image=photo, text="")
        label.image = photo
        label.pack()
        label.bind("<Double-Button-1>", lambda e: top.destroy())
        top.bind("<Double-Button-1>", lambda e: top.destroy())

    def update_progress_display(self):
        if self.total_items == 0:
            return
        ratio = self.processed_items / self.total_items
        self.progressbar.set(ratio)
        self.progress_label.configure(text=f"{self.processed_items}/{self.total_items} ({ratio:.0%})")
        if self.start_time:
            elapsed = time.time() - self.start_time
            if self.processed_items > 0:
                total_est = elapsed / (self.processed_items / self.total_items)
                remaining = total_est - elapsed
                self.time_label.configure(text=f"已用: {self._fmt_time(elapsed)} | 预计: {self._fmt_time(total_est)} | 剩余: {self._fmt_time(remaining)}")

    @staticmethod
    def _fmt_time(sec):
        if sec < 0:
            sec = 0
        m, s = divmod(int(sec), 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    def show_login_alert(self):
        self.status_title.configure(text="状态：等待确认登录", text_color="red")
        self.login_btn.configure(state="normal")
        self.add_log(LogEntry("⚠️ 请在浏览器中登录，然后点击“确认已登录”！", level="error"))

    def finish_task(self):
        self.status_title.configure(text="状态：完成", text_color="green")
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.login_btn.configure(state="disabled")

    # ---------- 队列 ----------
    def process_queue(self):
        while not self.log_queue.empty():
            item = self.log_queue.get_nowait()
            if isinstance(item, LogEntry):
                self.add_log(item)
        self.after(100, self.process_queue)

    # ---------- 按钮操作 ----------
    def kill_chrome(self):
        if messagebox.askyesno("确认操作", "确定要强制结束所有 Chrome 进程吗？\n这可能会关闭您正在使用的浏览器页面。"):
            if kill_chrome_processes():
                messagebox.showinfo("提示", "已结束所有 chrome.exe 进程")
            else:
                messagebox.showerror("错误", "结束进程失败，请手动关闭")

    def confirm_login(self):
        self.login_confirmed.set()
        self.login_btn.configure(state="disabled")
        self.status_title.configure(text="状态：执行中...", text_color="blue")

    def stop_execution(self):
        self.stop_event.set()
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.login_btn.configure(state="disabled")
        self.status_title.configure(text="状态：已停止", text_color="red")

    def start_execution(self):
        excel = self.file_var.get()
        output = self.out_var.get()
        if not excel or not output:
            messagebox.showerror("错误", "请填写 Excel 路径和输出文件")
            return
        if output == "results.xlsx":
            output = f"results_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
            self.out_var.set(output)

        self._save_current_config()

        # 清空图片历史
        for child in self.image_scroll_frame.winfo_children():
            if child != self.image_scroll_frame.winfo_children()[0]:
                child.destroy()

        # 读取 Excel & 断点续传
        try:
            if os.path.exists(output):
                df = pd.read_excel(output, sheet_name=0)
                self.add_log(LogEntry(f"从输出文件恢复续传，共 {len(df)} 行"))
            else:
                df = pd.read_excel(excel, sheet_name=0)
                if "回答" not in df.columns:
                    df["回答"] = ""
                self.add_log(LogEntry(f"首次运行，读取 {len(df)} 行"))
            if df.empty:
                messagebox.showerror("错误", "Excel 为空")
                return

            start_idx = 0
            def valid_answer(v):
                return pd.notna(v) and str(v).strip() and not str(v).startswith("ERROR:")

            skip_col = self.skip_col_var.get() if self.enable_skip.get() else None
            skip_vals = [v.strip() for v in self.skip_values_var.get().split(',') if v.strip()] if self.enable_skip.get() else []

            for i in range(len(df)):
                if valid_answer(df.at[i, "回答"]):
                    start_idx = i + 1
                    continue
                if skip_col and skip_vals and pd.notna(df.at[i, skip_col]) and str(df.at[i, skip_col]).strip() in skip_vals:
                    if not valid_answer(df.at[i, "回答"]):
                        df.at[i, "回答"] = self.skip_mark_var.get()
                        df.to_excel(output, index=False)
                    start_idx = i + 1
                    continue
                break
            else:
                messagebox.showinfo("提示", "所有行已处理，无需续传")
                return

            if start_idx:
                self.add_log(LogEntry(f"续传：从第 {start_idx + 1} 行开始"))

        except Exception as e:
            messagebox.showerror("Excel 读取失败", str(e))
            return

        # 重置监控
        self.log_entries.clear()
        for w in self.log_widgets:
            w.destroy()
        self.log_widgets.clear()
        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0", "end")
        self.detail_text.configure(state="disabled")

        self.progressbar.set(0)
        self.processed_items = start_idx
        self.total_items = len(df)
        self.start_time = time.time()
        self.update_progress_display()

        self.stop_event.clear()
        self.login_confirmed.clear()

        # 创建日志子目录
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        self.current_log_dir = os.path.join(self.base_log_dir, f"logs_{timestamp}")
        os.makedirs(self.current_log_dir, exist_ok=True)
        self._update_info_panel()

        # 收集模型特定参数
        model_args = {}
        platform = self.platform_var.get()
        if platform == "DeepSeek":
            model_args = {
                "mode": self.mode_var.get(),
                "deep_think": self.deep_var.get(),
                "smart_search": self.search_var.get()
            }
        elif platform == "豆包":
            model_args = {
                "doubao_mode": self.doubao_mode_var.get()
            }

        self.worker = WorkerThread(
            excel,
            self.context_col_var.get() if self.enable_context.get() else None,
            self.ques_col_var.get(),
            output,
            self.prefix_box.get("1.0", "end-1c"),
            self.context_label_var.get(),
            self.ques_label_var.get(),
            self.enable_context.get(),
            self.browser_mode_var.get(),
            int(self.width_var.get()) if self.width_var.get().isdigit() else 1920,
            int(self.height_var.get()) if self.height_var.get().isdigit() else 1080,
            int(self.x_var.get()) if self.x_var.get().isdigit() else 0,
            int(self.y_var.get()) if self.y_var.get().isdigit() else 0,
            self.log_queue,
            self.stop_event,
            self.current_log_dir,
            self.skip_login_var.get(),
            self.enable_skip.get(),
            self.skip_col_var.get() if self.enable_skip.get() else None,
            self.skip_values_var.get(),
            self.skip_mark_var.get(),
            platform=platform,
            model_args=model_args
        )
        self.worker.start_idx = start_idx
        self.worker.existing_df = df
        self.worker.set_login_event(self.login_confirmed)

        if not self.skip_login_var.get():
            self.login_btn.configure(state="normal")
        else:
            self.login_confirmed.set()

        self.worker.start()
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.status_title.configure(text="状态：执行中...", text_color="blue")

    # ---------- 预设指令管理 ----------
    def _insert_preset_prefix(self):
        selected = self.preset_var.get()
        if not selected or selected not in self.preset_prefixes:
            return
        content = self.preset_prefixes[selected]
        try:
            pos = self.prefix_box.index("insert")
        except:
            pos = "end-1c"
        self.prefix_box.insert(pos, content)
        self.prefix_box.focus_set()

    def _save_current_prefix_as_preset(self):
        current_content = self.prefix_box.get("1.0", "end-1c").strip()
        if not current_content:
            messagebox.showwarning("提示", "当前前缀指令为空，无法保存")
            return

        save_win = ctk.CTkToplevel(self)
        save_win.title("保存为预设")
        save_win.geometry("500x400")
        save_win.resizable(True, True)
        save_win.grab_set()
        save_win.update_idletasks()
        x = (save_win.winfo_screenwidth() - 500) // 2
        y = (save_win.winfo_screenheight() - 400) // 2
        save_win.geometry(f"500x400+{x}+{y}")

        main_frame = ctk.CTkFrame(save_win)
        main_frame.pack(fill="both", expand=True, padx=10, pady=10)

        name_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        name_frame.pack(fill="x", pady=5)
        ctk.CTkLabel(name_frame, text="预设名称:", width=80).pack(side="left", padx=5)
        name_entry = ctk.CTkEntry(name_frame, width=300)
        name_entry.pack(side="left", fill="x", expand=True, padx=5)

        ctk.CTkLabel(main_frame, text="指令内容:", anchor="w").pack(anchor="w", pady=(10,0))
        content_text = ctk.CTkTextbox(main_frame, height=200, wrap="word")
        content_text.pack(fill="both", expand=True, pady=5)
        content_text.insert("1.0", current_content)

        btn_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        btn_frame.pack(fill="x", pady=10)

        def do_save():
            new_name = name_entry.get().strip()
            new_content = content_text.get("1.0", "end-1c").strip()
            if not new_name:
                messagebox.showwarning("提示", "预设名称不能为空", parent=save_win)
                return
            if not new_content:
                messagebox.showwarning("提示", "指令内容不能为空", parent=save_win)
                return
            if new_name in self.preset_prefixes:
                if not messagebox.askyesno("覆盖确认", f"预设“{new_name}”已存在，是否覆盖？", parent=save_win):
                    return
            self.preset_prefixes[new_name] = new_content
            self._refresh_preset_menu()
            self.preset_var.set(new_name)
            self._save_current_config()
            self.add_log(LogEntry(f"已保存预设指令：{new_name}", level="info"))
            save_win.destroy()

        ctk.CTkButton(btn_frame, text="确定", width=80, command=do_save).pack(side="left", padx=10)
        ctk.CTkButton(btn_frame, text="取消", width=80, command=save_win.destroy).pack(side="left", padx=10)

        suggest_name = current_content[:20].replace('\n', ' ').strip()
        if suggest_name:
            name_entry.insert(0, suggest_name)
        name_entry.focus_set()

    def _edit_selected_preset(self):
        old_name = self.preset_var.get()
        if not old_name or old_name not in self.preset_prefixes:
            messagebox.showwarning("提示", "请先选择一个预设指令")
            return

        old_content = self.preset_prefixes[old_name]

        edit_win = ctk.CTkToplevel(self)
        edit_win.title("编辑预设指令")
        edit_win.update_idletasks()
        screen_width = edit_win.winfo_screenwidth()
        screen_height = edit_win.winfo_screenheight()
        x = (screen_width - 500) // 2
        y = (screen_height - 400) // 2
        edit_win.geometry(f"500x400+{x}+{y}")
        edit_win.resizable(True, True)
        edit_win.grab_set()

        main_frame = ctk.CTkFrame(edit_win)
        main_frame.pack(fill="both", expand=True, padx=10, pady=10)

        name_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        name_frame.pack(fill="x", pady=5)
        ctk.CTkLabel(name_frame, text="名称:", width=60).pack(side="left", padx=5)
        name_entry = ctk.CTkEntry(name_frame, width=300)
        name_entry.insert(0, old_name)
        name_entry.pack(side="left", fill="x", expand=True, padx=5)

        ctk.CTkLabel(main_frame, text="指令内容:", anchor="w").pack(anchor="w", pady=(10,0))
        content_text = ctk.CTkTextbox(main_frame, height=200, wrap="word")
        content_text.pack(fill="both", expand=True, pady=5)
        content_text.insert("1.0", old_content)

        btn_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        btn_frame.pack(fill="x", pady=10)

        def save_changes():
            new_name = name_entry.get().strip()
            new_content = content_text.get("1.0", "end-1c").strip()
            if not new_name:
                messagebox.showwarning("提示", "名称不能为空", parent=edit_win)
                return
            if not new_content:
                messagebox.showwarning("提示", "指令内容不能为空", parent=edit_win)
                return
            if new_name != old_name and new_name in self.preset_prefixes:
                if not messagebox.askyesno("覆盖确认", f"预设“{new_name}”已存在，是否覆盖？", parent=edit_win):
                    return
            del self.preset_prefixes[old_name]
            self.preset_prefixes[new_name] = new_content
            self._refresh_preset_menu()
            self.preset_var.set(new_name)
            self._save_current_config()
            self.add_log(LogEntry(f"已编辑预设：{old_name} → {new_name}", level="info"))
            edit_win.destroy()

        def delete_preset():
            if len(self.preset_prefixes) <= 1:
                messagebox.showwarning("提示", "至少保留一个预设，无法删除", parent=edit_win)
                return
            if messagebox.askyesno("确认删除", f"确定要删除预设“{old_name}”吗？", parent=edit_win):
                del self.preset_prefixes[old_name]
                self._refresh_preset_menu()
                if self.preset_names:
                    self.preset_var.set(self.preset_names[0])
                self._save_current_config()
                self.add_log(LogEntry(f"已删除预设指令：{old_name}", level="info"))
                edit_win.destroy()

        ctk.CTkButton(btn_frame, text="确定", width=80, command=save_changes).pack(side="left", padx=10)
        ctk.CTkButton(btn_frame, text="取消", width=80, command=edit_win.destroy).pack(side="left", padx=10)
        ctk.CTkButton(btn_frame, text="删除", width=80, fg_color="#d32f2f", hover_color="#b71c1c", command=delete_preset).pack(side="right", padx=10)

        name_entry.focus_set()

    def _clear_prefix_box(self):
        self.prefix_box.delete("1.0", "end")
        self.add_log(LogEntry("已清空前缀指令框", level="info"))

    def _on_preset_selected(self, choice):
        content = self.preset_prefixes.get(choice, "")
        if content:
            self.prefix_box.delete("1.0", "end")
            self.prefix_box.insert("1.0", content)
            self.add_log(LogEntry(f"已应用预设指令：{choice}", level="info"))

    def _on_enable_skip_changed(self):
        self.toggle_skip()
        if self.enable_skip.get():
            if self.file_var.get():
                self.load_columns()
            else:
                messagebox.showinfo("提示", "请先选择 Excel 文件，然后再次勾选以加载列名")

    def _refresh_preset_menu(self):
        self.preset_names = list(self.preset_prefixes.keys())
        self.preset_menu.configure(values=self.preset_names)
        if self.preset_names and self.preset_var.get() not in self.preset_names:
            self.preset_var.set(self.preset_names[0])

    # ---------- 配置保存 ----------
    def _save_current_config(self):
        config_update = {
            "excel_path": self.file_var.get(),
            "output_path": self.out_var.get(),
            "prefix": self.prefix_box.get("1.0", "end-1c"),
            "enable_context": self.enable_context.get(),
            "context_col": self.context_col_var.get(),
            "question_col": self.ques_col_var.get(),
            "context_label": self.context_label_var.get(),
            "question_label": self.ques_label_var.get(),
            "browser_mode": self.browser_mode_var.get(),
            "browser_width": int(self.width_var.get()) if self.width_var.get().isdigit() else 1920,
            "browser_height": int(self.height_var.get()) if self.height_var.get().isdigit() else 1080,
            "browser_x": int(self.x_var.get()) if self.x_var.get().isdigit() else 0,
            "browser_y": int(self.y_var.get()) if self.y_var.get().isdigit() else 0,
            "skip_login": self.skip_login_var.get(),
            "enable_skip": self.enable_skip.get(),
            "skip_col": self.skip_col_var.get(),
            "skip_values": self.skip_values_var.get(),
            "skip_mark": self.skip_mark_var.get(),
            "preset_prefixes": self.preset_prefixes,
            "active_filters": self.active_filters,
            "model_platform": self.platform_var.get(),
            "window_geometry": self.config.get("window_geometry", "1200x750"),
            "window_maximized": self.config.get("window_maximized", False)
        }
        # 添加平台特有的配置
        if self.platform_var.get() == "DeepSeek":
            config_update["mode"] = self.mode_var.get()
            config_update["deep_think"] = self.deep_var.get()
            config_update["smart_search"] = self.search_var.get()
        elif self.platform_var.get() == "豆包":
            config_update["doubao_mode"] = self.doubao_mode_var.get()
        self.config.update(config_update)
        save_config(self.config)

    def on_closing(self):
        if self.state() == 'zoomed':
            self.config["window_maximized"] = True
        else:
            self.config["window_maximized"] = False
            self.config["window_geometry"] = self.geometry()
        self._save_current_config()
        self.destroy()