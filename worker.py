import datetime
import threading
import time
import os
import traceback
import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager

from utils import LogEntry


def take_screenshot(driver, log_dir, name):
    try:
        if not log_dir:
            return None
        os.makedirs(log_dir, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%H%M%S_%f")[:-3]
        filename = f"screenshot_{name}_{timestamp}.png"
        filepath = os.path.join(log_dir, filename)
        driver.save_screenshot(filepath)
        return filepath
    except Exception as e:
        print(f"截图失败: {e}")
        return None


class WorkerThread(threading.Thread):
    def __init__(self, excel_path, context_col, question_col, output_path,
                 prefix, context_label, question_label, has_context,
                 browser_mode, browser_width=None, browser_height=None,
                 browser_x=None, browser_y=None,
                 log_queue=None, stop_event=None,
                 log_dir=None, skip_login=False,
                 enable_skip=False, skip_col=None, skip_values=None, skip_mark="SKIPPED",
                 platform="DeepSeek", model_args=None):
        super().__init__(daemon=True)
        self.excel_path = excel_path
        self.context_col = context_col
        self.question_col = question_col
        self.output_path = output_path
        self.prefix = prefix
        self.context_label = context_label
        self.question_label = question_label
        self.has_context = has_context
        self.browser_mode = browser_mode
        self.browser_width = browser_width or 1920
        self.browser_height = browser_height or 1080
        self.browser_x = browser_x or 0
        self.browser_y = browser_y or 0
        self.log_queue = log_queue
        self.stop_event = stop_event
        self.log_dir = log_dir
        self.skip_login = skip_login
        self.enable_skip = enable_skip
        self.skip_col = skip_col
        self.skip_values = [v.strip() for v in skip_values.split(',') if v.strip()] if skip_values else []
        self.skip_mark = skip_mark
        self.driver = None
        self.total = 0
        self.start_idx = 0
        self.existing_df = None
        self.platform = platform
        self.model_args = model_args or {}

        # 文件日志
        self.file_logger = None
        if self.log_dir:
            os.makedirs(self.log_dir, exist_ok=True)
            log_filename = f"worker_log_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            self.file_log_path = os.path.join(self.log_dir, log_filename)
            try:
                self.file_logger = open(self.file_log_path, "w", encoding="utf-8")
                self.log(LogEntry(f"工作日志文件已创建：{self.file_log_path}", level="info"))
            except Exception as e:
                print(f"无法创建日志文件: {e}")

    def log(self, entry: LogEntry):
        if self.log_queue:
            self.log_queue.put(entry)
        if self.file_logger:
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            brief = entry.brief[:200]
            detail = entry.detail[:500] if entry.detail else ""
            level = entry.level
            line = f"[{timestamp}] [{level.upper()}] {brief}"
            if entry.image_path:
                line += f" [图片: {entry.image_path}]"
            if entry.index is not None:
                line += f" [行号: {entry.index+1}]"
            self.file_logger.write(line + "\n")
            if detail and detail != brief:
                self.file_logger.write(f"    详情: {detail}\n")
            self.file_logger.flush()

    def close_file_logger(self):
        if self.file_logger:
            self.file_logger.close()
            self.file_logger = None

    def run(self):
        self.log(LogEntry("Worker 线程已启动", level="info"))
        # 读取或复用 DataFrame
        if self.existing_df is not None:
            df = self.existing_df
            if self.enable_skip and self.skip_col:
                if self.skip_col not in df.columns:
                    self.log(LogEntry(f"错误：跳过列 '{self.skip_col}' 不在 Excel 中", level="error"))
                    self.enable_skip = False
                else:
                    self.log(LogEntry(f"跳过规则已启用：列='{self.skip_col}'，跳过值={self.skip_values}，标记='{self.skip_mark}'", level="info"))
        else:
            try:
                self.log(LogEntry("正在读取 Excel...", level="progress"))
                df = pd.read_excel(self.excel_path, sheet_name=0)
                if "回答" not in df.columns:
                    df["回答"] = ""
                self.log(LogEntry(f"读取成功，共 {len(df)} 行数据"))
            except Exception as e:
                self.log(LogEntry("读取 Excel 失败", str(e), level="error"))
                self.close_file_logger()
                return
        self.total = len(df)

        # 检查必要列
        missing = []
        if self.has_context and self.context_col not in df.columns:
            missing.append(self.context_col)
        if self.question_col not in df.columns:
            missing.append(self.question_col)
        if self.enable_skip and self.skip_col and self.skip_col not in df.columns:
            missing.append(f"跳过列 '{self.skip_col}'")
        if missing:
            self.log(LogEntry("Excel 缺少列", f"缺少：{', '.join(missing)}", level="error"))
            self.close_file_logger()
            return

        # 统计跳过数量
        total_skipped = 0
        for idx in range(self.start_idx, self.total):
            if self.should_skip_row(df, idx):
                total_skipped += 1

        # 判断是否需要浏览器
        need_browser = False
        skip_count = 0
        for idx in range(self.start_idx, self.total):
            if self.should_skip_row(df, idx):
                skip_count += 1
                if df.at[idx, "回答"] != self.skip_mark:
                    df.at[idx, "回答"] = self.skip_mark
                    try:
                        df.to_excel(self.output_path, index=False)
                    except:
                        pass
                self.log(LogEntry(f"跳过第 {idx+1} 行 (已跳过 {skip_count}/{total_skipped} 条) - 原因：{self.skip_col}={df.at[idx, self.skip_col]}",
                                  level="info", index=idx))
                self.log_queue.put(LogEntry("进度更新", level="progress", group="progress_update", index=idx+1))
                continue
            need_browser = True
            break

        if need_browser:
            self.log(LogEntry("启动浏览器..."))
            try:
                opts = Options()
                profile_dir = os.path.join(os.path.expanduser("~"), f"{self.platform.lower()}_chrome_profile")
                opts.add_argument(f"--user-data-dir={profile_dir}")
                opts.add_experimental_option("detach", True)

                service = Service(ChromeDriverManager().install())
                self.driver = webdriver.Chrome(service=service, options=opts)
                self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

                if self.browser_mode == "maximized":
                    self.driver.maximize_window()
                elif self.browser_mode == "minimized":
                    self.driver.minimize_window()
                elif self.browser_mode == "custom":
                    self.driver.set_window_rect(self.browser_x, self.browser_y, self.browser_width, self.browser_height)

                # 根据平台打开不同网址
                if self.platform == "DeepSeek":
                    self.driver.get("https://chat.deepseek.com/")
                else:  # 豆包
                    self.driver.get("https://www.doubao.com/chat/")   # 请确认豆包实际聊天页面URL

            except Exception as e:
                self.log(LogEntry("浏览器启动失败", str(e), level="error"))
                self.close_file_logger()
                return

            # 登录等待
            if not self.skip_login:
                self.log(LogEntry("请手动登录，然后点击“确认已登录”按钮", level="special"))
                self.log_queue.put(LogEntry("WAIT_LOGIN", level="special"))
                while not hasattr(self, 'login_confirmed') or not self.login_confirmed.is_set():
                    if self.stop_event and self.stop_event.is_set():
                        self.driver.quit()
                        self.close_file_logger()
                        return
                    time.sleep(0.2)
                self.log(LogEntry("用户已确认登录", level="info"))
                try:
                    self.driver.set_window_rect(-2000, -2000, 800, 600)
                    self.log(LogEntry("浏览器窗口已移至屏幕外", level="info"))
                except Exception as e:
                    self.log(LogEntry(f"移动窗口失败: {e}", level="warn"))
            else:
                self.log(LogEntry("跳过登录检查，直接开始执行", level="info"))
                time.sleep(3)
                try:
                    self.driver.set_window_rect(-2000, -2000, 800, 600)
                except:
                    pass

            self.log(LogEntry("开始执行自动化..."))

            # 根据平台执行不同的自动化逻辑
            if self.platform == "DeepSeek":
                self._deepseek_automation(df)
            else:
                self._doubao_automation(df)

        else:
            # 没有需要处理的行，直接完成
            self.log(LogEntry("没有需要处理的新行", level="info"))

        self.log(LogEntry(f"全部完成！结果已保存至 {self.output_path}", file_path=self.output_path))
        self.log_queue.put(LogEntry("FINISHED", level="special"))
        if self.driver:
            self.driver.quit()
        self.close_file_logger()

    # ----------------------------- DeepSeek 自动化 -----------------------------
    def _deepseek_automation(self, df):
        # 创建新对话
        try:
            new_chat = self.driver.find_element(By.XPATH, "//button[contains(.,'新对话') or contains(@aria-label,'新对话')]")
            new_chat.click()
            time.sleep(2)
            self.log(LogEntry("已点击“新对话”"))
        except:
            self.log(LogEntry("未找到“新对话”按钮", level="warn"))

        # 设置模式
        self._deepseek_set_model_mode()
        self._deepseek_set_toggles()

        # 逐条处理
        total_skipped = sum(1 for idx in range(self.start_idx, self.total) if self.should_skip_row(df, idx))
        skip_count = 0
        for idx in range(self.start_idx, self.total):
            if self.stop_event.is_set():
                self.log(LogEntry("用户中止执行", level="error"))
                break

            if self.should_skip_row(df, idx):
                skip_count += 1
                if df.at[idx, "回答"] != self.skip_mark:
                    df.at[idx, "回答"] = self.skip_mark
                    df.to_excel(self.output_path, index=False)
                self.log(LogEntry(f"根据跳过规则，跳过第 {idx+1} 行 (已跳过 {skip_count}/{total_skipped} 条)",
                                  level="info", group="progress_start", index=idx))
                self.log_queue.put(LogEntry("进度更新", level="progress", group="progress_update", index=idx+1))
                continue

            current_answer = df.at[idx, "回答"]
            if pd.notna(current_answer) and str(current_answer).strip() and not str(current_answer).startswith("ERROR:"):
                self.log(LogEntry(f"第 {idx+1} 行已有有效回答，跳过", level="info", group="progress_start", index=idx))
                self.log_queue.put(LogEntry("进度更新", level="progress", group="progress_update", index=idx+1))
                continue

            row = df.iloc[idx]
            context = str(row[self.context_col]) if self.has_context and self.context_col else None
            question = str(row[self.question_col])
            prompt = self._format_prompt(context, question)

            self.log(LogEntry(f"处理第 {idx+1}/{self.total} 条",
                              f"完整提示词：\n{prompt}",
                              level="progress", group="progress_start", index=idx))

            try:
                # 输入并发送
                ta = self._deepseek_get_textarea()
                self._deepseek_fill_textarea(ta, prompt)
                ss_after_fill = take_screenshot(self.driver, self.log_dir, f"after_fill_idx{idx}")
                if ss_after_fill:
                    self.log(LogEntry("填入提示词后截图", image_path=ss_after_fill, level="info", group="progress_detail", index=idx))

                time.sleep(0.5)
                self._deepseek_send()
                time.sleep(1)
                ss_after_send = take_screenshot(self.driver, self.log_dir, f"after_send_idx{idx}")
                if ss_after_send:
                    self.log(LogEntry("点击发送后截图", image_path=ss_after_send, level="info", group="progress_detail", index=idx))

                answer = self._deepseek_wait_for_answer(idx)
                df.at[idx, "回答"] = answer
                self.log(LogEntry("模型回答", answer, level="info", group="progress_detail", index=idx))
                self.log(LogEntry(f"回答长度：{len(answer)}", group="progress_detail", index=idx))

            except Exception as e:
                err = traceback.format_exc()
                self.log(LogEntry(f"处理第 {idx+1} 条失败", err, level="error", group="progress_detail", index=idx))
                df.at[idx, "回答"] = f"ERROR: {e}"

            df.to_excel(self.output_path, index=False)
            self.log_queue.put(LogEntry("进度更新", level="progress", group="progress_update", index=idx+1))
            time.sleep(3)

        df.to_excel(self.output_path, index=False)

    def _deepseek_set_model_mode(self):
        mode = self.model_args.get("mode", "expert")
        try:
            fast = self.driver.find_element(By.XPATH, "//div[@data-model-type='default' and @role='radio']")
            expert = self.driver.find_element(By.XPATH, "//div[@data-model-type='expert' and @role='radio']")
            if mode == "fast" and fast.get_attribute("aria-checked") != "true":
                fast.click()
                self.log(LogEntry("已切换至快速模式"))
            elif mode == "expert" and expert.get_attribute("aria-checked") != "true":
                expert.click()
                self.log(LogEntry("已切换至专家模式"))
            else:
                self.log(LogEntry(f"已经处于{'快速' if mode=='fast' else '专家'}模式"))
        except:
            self.log(LogEntry("未找到模式选择按钮", level="warn"))

    def _deepseek_set_toggles(self):
        toggles = {"深度思考": self.model_args.get("deep_think", True),
                   "智能搜索": self.model_args.get("smart_search", True)}
        for name, enable in toggles.items():
            try:
                btn = self.driver.find_element(By.XPATH, f"//div[@role='button' and contains(.,'{name}')]")
                cur = btn.get_attribute("aria-pressed") == "true"
                if enable and not cur:
                    btn.click()
                    time.sleep(0.3)
                    self.log(LogEntry(f"已开启“{name}”"))
                elif not enable and cur:
                    btn.click()
                    time.sleep(0.3)
                    self.log(LogEntry(f"已关闭“{name}”"))
                else:
                    self.log(LogEntry(f"“{name}”状态已符合"))
            except:
                self.log(LogEntry(f"未找到“{name}”开关", level="warn"))

    def _deepseek_get_textarea(self):
        selectors = ["textarea._27c9245", "textarea.ds-scroll-area", "textarea[placeholder*='发送消息']"]
        for sel in selectors:
            try:
                return WebDriverWait(self.driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, sel)))
            except:
                continue
        return self.driver.find_element(By.TAG_NAME, "textarea")

    def _deepseek_fill_textarea(self, ta, text):
        self.driver.execute_script("""
            var elem = arguments[0];
            var nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
            nativeSetter.call(elem, '');
            var inputEvent = new Event('input', { bubbles: true });
            elem.dispatchEvent(inputEvent);
        """, ta)
        time.sleep(0.3)
        self.driver.execute_script("""
            var elem = arguments[0];
            var text = arguments[1];
            var nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
            nativeSetter.call(elem, text);
            var inputEvent = new Event('input', { bubbles: true });
            elem.dispatchEvent(inputEvent);
            var changeEvent = new Event('change', { bubbles: true });
            elem.dispatchEvent(changeEvent);
        """, ta, text)

    def _deepseek_send(self):
        xpaths = ["//button[@aria-label='发送']", "//button[@aria-label='send']", "//button[contains(@class, 'send')]"]
        for xp in xpaths:
            try:
                btn = WebDriverWait(self.driver, 3).until(EC.element_to_be_clickable((By.XPATH, xp)))
                if btn.is_enabled():
                    btn.click()
                    self.log(LogEntry("点击发送按钮"))
                    return
            except:
                continue
        self.log(LogEntry("未找到发送按钮，尝试模拟回车", level="warn"))
        try:
            ta = self.driver.find_element(By.CSS_SELECTOR, "textarea")
            self.driver.execute_script("""
                var event = new KeyboardEvent('keydown', {
                    key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true
                });
                arguments[0].dispatchEvent(event);
            """, ta)
            self.log(LogEntry("已模拟回车键"))
        except Exception as e:
            self.log(LogEntry(f"模拟回车失败: {e}", level="error"))

    def _deepseek_wait_for_answer(self, idx, timeout=300):
        def get_assistant_text():
            selectors = [
                "div.assistant-message",
                "div[class*='assistant']",
                "div.ds-markdown",
                "//div[contains(@class,'message') and contains(@class,'assistant')]"
            ]
            for sel in selectors:
                try:
                    if sel.startswith("//"):
                        msgs = self.driver.find_elements(By.XPATH, sel)
                    else:
                        msgs = self.driver.find_elements(By.CSS_SELECTOR, sel)
                    if msgs:
                        return msgs[-1].text
                except:
                    continue
            return ""

        self.log(LogEntry("等待模型开始生成...", level="progress"))
        start_text = get_assistant_text()
        start_time = time.time()
        screenshot_taken = False
        while time.time() - start_time < 30:
            if self.stop_event and self.stop_event.is_set():
                return ""
            current = get_assistant_text()
            if current != start_text and len(current) > 0:
                self.log(LogEntry("检测到模型开始输出内容", level="progress"))
                if not screenshot_taken:
                    ss_path = take_screenshot(self.driver, self.log_dir, f"start_output_idx{idx}")
                    self.log(LogEntry("开始输出截图", image_path=ss_path, level="info", group="progress_detail", index=idx))
                    screenshot_taken = True
                break
            time.sleep(0.5)
        else:
            self.log(LogEntry("30秒内未检测到回答内容变化，可能生成失败", level="warn"))
            return get_assistant_text()

        deep_think = self.model_args.get("deep_think", True)
        if not deep_think:
            self.log(LogEntry("快速模式：等待回答内容稳定（短时）", level="progress"))
            last_text = get_assistant_text()
            stable_count = 0
            for _ in range(30):
                if self.stop_event and self.stop_event.is_set():
                    return last_text
                time.sleep(0.2)
                cur_text = get_assistant_text()
                if cur_text == last_text and len(cur_text) > 0:
                    stable_count += 1
                    if stable_count >= 2:
                        self.log(LogEntry("快速模式：回答已稳定", level="progress"))
                        break
                else:
                    stable_count = 0
                    last_text = cur_text
            else:
                self.log(LogEntry("快速模式：等待稳定超时，使用当前内容", level="warn"))
            return last_text
        else:
            self.log(LogEntry("深度思考模式：等待生成内容完全稳定...", level="progress"))
            last_text = get_assistant_text()
            stable_count = 0
            start_stable = time.time()
            while time.time() - start_stable < timeout:
                if self.stop_event and self.stop_event.is_set():
                    return last_text
                time.sleep(1.0)
                cur_text = get_assistant_text()
                if cur_text == last_text and len(cur_text) > 0:
                    stable_count += 1
                else:
                    stable_count = 0
                    last_text = cur_text
                if stable_count >= 5:
                    self.log(LogEntry("深度思考模式：文本已稳定，生成结束", level="progress"))
                    ss_path = take_screenshot(self.driver, self.log_dir, f"stable_idx{idx}")
                    self.log(LogEntry("生成稳定截图", image_path=ss_path, level="info", index=idx))
                    break
            else:
                self.log(LogEntry(f"等待文本稳定超时({timeout}秒)，使用当前内容", level="warn"))
            return last_text

    # ----------------------------- 豆包自动化 -----------------------------
    def _doubao_automation(self, df):
        # 等待页面加载完成
        time.sleep(3)
        # 尝试找到输入框（常见的豆包输入框样式）
        textarea = None
        try:
            textarea = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "textarea.semi-input-textarea, textarea[placeholder*='发消息']"))
            )
        except:
            try:
                textarea = self.driver.find_element(By.TAG_NAME, "textarea")
            except:
                self.log(LogEntry("未找到输入框", level="error"))
                return

        # 设置豆包模式（快速/思考/专家）
        doubao_mode = self.model_args.get("doubao_mode", "快速")
        self._doubao_set_mode(doubao_mode)

        # 逐条处理
        total_skipped = sum(1 for idx in range(self.start_idx, self.total) if self.should_skip_row(df, idx))
        skip_count = 0
        for idx in range(self.start_idx, self.total):
            if self.stop_event.is_set():
                self.log(LogEntry("用户中止执行", level="error"))
                break

            if self.should_skip_row(df, idx):
                skip_count += 1
                if df.at[idx, "回答"] != self.skip_mark:
                    df.at[idx, "回答"] = self.skip_mark
                    df.to_excel(self.output_path, index=False)
                self.log(LogEntry(f"根据跳过规则，跳过第 {idx+1} 行 (已跳过 {skip_count}/{total_skipped} 条)",
                                  level="info", group="progress_start", index=idx))
                self.log_queue.put(LogEntry("进度更新", level="progress", group="progress_update", index=idx+1))
                continue

            current_answer = df.at[idx, "回答"]
            if pd.notna(current_answer) and str(current_answer).strip() and not str(current_answer).startswith("ERROR:"):
                self.log(LogEntry(f"第 {idx+1} 行已有有效回答，跳过", level="info", group="progress_start", index=idx))
                self.log_queue.put(LogEntry("进度更新", level="progress", group="progress_update", index=idx+1))
                continue

            row = df.iloc[idx]
            context = str(row[self.context_col]) if self.has_context and self.context_col else None
            question = str(row[self.question_col])
            prompt = self._format_prompt(context, question)

            self.log(LogEntry(f"处理第 {idx+1}/{self.total} 条",
                              f"完整提示词：\n{prompt}",
                              level="progress", group="progress_start", index=idx))

            try:
                # 清空并输入
                self.driver.execute_script("arguments[0].value = '';", textarea)
                time.sleep(0.3)
                self.driver.execute_script("arguments[0].value = arguments[1];", textarea, prompt)
                # 触发 input 事件
                self.driver.execute_script("var evt = new Event('input', { bubbles: true }); arguments[0].dispatchEvent(evt);", textarea)
                time.sleep(0.5)

                ss_after_fill = take_screenshot(self.driver, self.log_dir, f"doubao_fill_idx{idx}")
                if ss_after_fill:
                    self.log(LogEntry("填入提示词后截图", image_path=ss_after_fill, level="info", group="progress_detail", index=idx))

                # 发送消息
                self._doubao_send()
                time.sleep(1)
                ss_after_send = take_screenshot(self.driver, self.log_dir, f"doubao_send_idx{idx}")
                if ss_after_send:
                    self.log(LogEntry("点击发送后截图", image_path=ss_after_send, level="info", group="progress_detail", index=idx))

                # 等待回答
                answer = self._doubao_wait_for_answer(idx)
                df.at[idx, "回答"] = answer
                self.log(LogEntry("模型回答", answer, level="info", group="progress_detail", index=idx))
                self.log(LogEntry(f"回答长度：{len(answer)}", group="progress_detail", index=idx))

            except Exception as e:
                err = traceback.format_exc()
                self.log(LogEntry(f"处理第 {idx+1} 条失败", err, level="error", group="progress_detail", index=idx))
                df.at[idx, "回答"] = f"ERROR: {e}"

            df.to_excel(self.output_path, index=False)
            self.log_queue.put(LogEntry("进度更新", level="progress", group="progress_update", index=idx+1))
            time.sleep(3)

        df.to_excel(self.output_path, index=False)

    def _doubao_set_mode(self, mode):
        """豆包的模式切换：点击当前显示模式的按钮，然后在下拉菜单中选择"""
        try:
            # 定位模式选择按钮（显示“快速”等文字）
            mode_btn = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(@class,'skill-bar-button')]//div[contains(text(),'快速') or contains(text(),'思考') or contains(text(),'专家')]"))
            )
            current_text = mode_btn.text.strip()
            if current_text == mode:
                self.log(LogEntry(f"豆包模式已经是 {mode}"))
                return
            mode_btn.click()
            time.sleep(0.5)
            # 选择目标模式
            target = self.driver.find_element(By.XPATH, f"//div[@role='menu']//div[contains(text(),'{mode}')]")
            target.click()
            self.log(LogEntry(f"已切换豆包模式至：{mode}"))
            time.sleep(0.5)
        except Exception as e:
            self.log(LogEntry(f"设置豆包模式失败: {e}", level="warn"))

    def _doubao_send(self):
        # 尝试多种发送按钮定位
        try:
            send_btn = self.driver.find_element(By.XPATH, "//button[@aria-label='发送']")
            if send_btn.is_enabled():
                send_btn.click()
                self.log(LogEntry("点击发送按钮"))
                return
        except:
            pass
        try:
            send_btn = self.driver.find_element(By.XPATH, "//button[contains(@class,'send')]")
            if send_btn.is_enabled():
                send_btn.click()
                self.log(LogEntry("点击发送按钮 (class send)"))
                return
        except:
            pass
        # 尝试模拟回车
        try:
            ta = self.driver.find_element(By.CSS_SELECTOR, "textarea")
            self.driver.execute_script("""
                var event = new KeyboardEvent('keydown', {key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true});
                arguments[0].dispatchEvent(event);
            """, ta)
            self.log(LogEntry("已模拟回车键发送"))
        except Exception as e:
            self.log(LogEntry(f"发送消息失败: {e}", level="error"))

    def _doubao_wait_for_answer(self, idx, timeout=300):
        """等待豆包的助手回答，基于消息列表新增内容"""
        # 获取当前最后一条消息（可能是用户刚刚发出的）
        def get_last_assistant_text():
            # 尝试多种选择器定位助手消息
            selectors = [
                "div[class*='assistant-message']",
                "div[class*='message-receive']",
                "div[data-message-id] div[class*='markdown']",
                "//div[contains(@class,'message') and contains(@class,'assistant')]//div[contains(@class,'markdown')]"
            ]
            for sel in selectors:
                try:
                    if sel.startswith("//"):
                        elems = self.driver.find_elements(By.XPATH, sel)
                    else:
                        elems = self.driver.find_elements(By.CSS_SELECTOR, sel)
                    if elems:
                        return elems[-1].text
                except:
                    continue
            return ""

        self.log(LogEntry("等待豆包回答...", level="progress"))
        start_text = get_last_assistant_text()
        start_time = time.time()
        screenshot_taken = False
        # 等待开始输出（文本变化）
        while time.time() - start_time < 60:
            if self.stop_event and self.stop_event.is_set():
                return ""
            current = get_last_assistant_text()
            if len(current) > 0 and (start_text == "" or current != start_text):
                self.log(LogEntry("检测到豆包开始输出内容", level="progress"))
                if not screenshot_taken:
                    ss_path = take_screenshot(self.driver, self.log_dir, f"doubao_start_idx{idx}")
                    self.log(LogEntry("开始输出截图", image_path=ss_path, level="info", group="progress_detail", index=idx))
                    screenshot_taken = True
                break
            time.sleep(0.5)
        else:
            self.log(LogEntry("等待输出超时，可能失败", level="warn"))
            return get_last_assistant_text()

        # 等待内容稳定（3秒内无变化）
        last_text = get_last_assistant_text()
        stable_count = 0
        stable_start = time.time()
        while time.time() - stable_start < timeout:
            if self.stop_event and self.stop_event.is_set():
                return last_text
            time.sleep(1.0)
            current = get_last_assistant_text()
            if current == last_text and len(current) > 0:
                stable_count += 1
                if stable_count >= 3:   # 3秒无变化认为稳定
                    self.log(LogEntry("豆包回答已稳定", level="progress"))
                    ss_path = take_screenshot(self.driver, self.log_dir, f"doubao_stable_idx{idx}")
                    self.log(LogEntry("稳定截图", image_path=ss_path, level="info", index=idx))
                    break
            else:
                stable_count = 0
                last_text = current
        else:
            self.log(LogEntry("等待稳定超时，使用当前内容", level="warn"))
        return last_text

    # ----------------------------- 公共辅助 -----------------------------
    def _format_prompt(self, history, question):
        parts = []
        if self.prefix:
            parts.append(self.prefix)
        if self.has_context and history is not None:
            parts.append(f"{self.context_label}：\n\n{history}")
        parts.append(f"{self.question_label}：\n\n{question}")
        return "\n\n".join(parts)

    def should_skip_row(self, df, idx):
        if not self.enable_skip or not self.skip_col or not self.skip_values:
            return False
        if self.skip_col not in df.columns:
            return False
        val = df.at[idx, self.skip_col]
        if pd.isna(val):
            return False
        val_str = str(val).strip()
        for skip_val in self.skip_values:
            if val_str == skip_val.strip():
                return True
        return False

    def set_login_event(self, event):
        self.login_confirmed = event