#!/usr/bin/env python3
"""
AI Agent 配置管理器
独立配置脚本，支持首次启动自动弹出，可从主脚本调用
"""

import os
import json
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
from typing import Dict, Any, List
import sys
import threading
import time
from ai_client import CONFIG_FILE



# ============================================================================
# 配置常量
# ============================================================================


DEFAULT_CONFIG = {
    "ollama": {
        "url": "http://localhost:11434/api/chat",
        "model": "gemma4:e4b",
        "max_tokens": 8192
    },
    "dashscope": {
        "api_key": "",
        "model": "qwen3.7-max-2026-06-08",
        "max_tokens": 8192
    },
    "deepseek": {
        "api_key": "",
        "model": "deepseek-v4-flash",
        "max_tokens": 8192
    },
    "zhipu": {
        "api_key": "",
        "model": "glm-4.5-flash",
        "max_tokens": 8192,
        "url": "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    },
    "tokenhub": {
        "api_key": "",
        "model": "hy3-preview",
        "max_tokens": 8192,
        "base_url": "https://tokenhub.tencentmaas.com/v1"
    },
    "general": {
        "temperature": 0.3,
        "timeout": 120,
        "main_backend": "ollama",
        "assistant_backend": "zhipu",
        "idle_threshold": 60,
        "learning_interval": 300,
        "embedding_model": "all-MiniLM-L6-v2",
        "embedding_min_similarity": 0.25,
        "embedding_top_k": 5,   
    }
}

# ============================================================================
# 语义模型检测函数
# ============================================================================

def check_embedding_model(model_name: str) -> Dict[str, Any]:
    """检测语义模型是否可用（直接加载验证）"""
    result = {"available": False, "dimension": 0, "error": None}
    
    try:
        os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
        from sentence_transformers import SentenceTransformer
        
        # 直接加载，sentence-transformers 自己找缓存
        model = SentenceTransformer(model_name)
        dim = model.get_sentence_embedding_dimension()
        model.encode("测试", normalize_embeddings=True)
        
        result["available"] = True
        result["dimension"] = dim
        
    except ImportError:
        result["error"] = "sentence-transformers 未安装"
    except Exception as e:
        result["error"] = str(e)
    
    return result

def download_embedding_model(model_name: str, progress_callback=None) -> bool:
    """
    下载语义模型（使用国内镜像），带进度回调
    
    Args:
        model_name: 模型名称
        progress_callback: 进度回调函数，接收 (percent, message)
    
    Returns:
        bool: 是否成功
    """
    try:
        os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
        
        if progress_callback:
            progress_callback(10, f"准备下载 {model_name}...")
        
        from sentence_transformers import SentenceTransformer
        
        if progress_callback:
            progress_callback(20, "正在连接镜像服务器...")
        
        # 下载模型
        model = SentenceTransformer(model_name)
        
        if progress_callback:
            progress_callback(70, "正在验证模型...")
        
        # 验证
        dim = model.get_sentence_embedding_dimension()
        model.encode("测试", normalize_embeddings=True)
        
        if progress_callback:
            progress_callback(100, f"下载完成 (维度: {dim})")
        
        return True
        
    except ImportError:
        if progress_callback:
            progress_callback(0, "sentence-transformers 未安装")
        return False
    except Exception as e:
        if progress_callback:
            progress_callback(0, f"下载失败: {str(e)[:60]}")
        return False

# ============================================================================
# 配置管理函数
# ============================================================================

def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
            for section, values in DEFAULT_CONFIG.items():
                if section not in config:
                    config[section] = values.copy()
                else:
                    for key, val in values.items():
                        if key not in config[section]:
                            config[section][key] = val
            return config
        except Exception as e:
            print(f"加载配置失败: {e}，使用默认配置")
            return DEFAULT_CONFIG.copy()
    else:
        return DEFAULT_CONFIG.copy()

def save_config(config: dict):
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"保存配置失败: {e}")
        return False

def config_exists() -> bool:
    if not os.path.exists(CONFIG_FILE):
        return False
    try:
        config = load_config()
        required = ["ollama", "dashscope", "deepseek", "zhipu", "tokenhub", "general"]
        for section in required:
            if section not in config:
                return False
        return True
    except:
        return False

def get_ollama_api_key() -> str:
    return ""

def get_ollama_models() -> List[str]:
    try:
        import requests
        resp = requests.get("http://localhost:11434/api/tags", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            models = [model.get('name', '') for model in data.get('models', [])]
            return models if models else []
    except Exception as e:
        print(f"获取 Ollama 模型列表失败: {e}")
    return []

# ============================================================================
# 配置界面 - 左右两栏布局
# ============================================================================

class ConfigWindow:
    def __init__(self, master=None, on_save_callback=None):
        self.on_save_callback = on_save_callback
        self.config = load_config()
        self.entries = {}
        
        if master is None:
            self.window = tk.Tk()
            self.window.title("AI Agent 配置")
            self.window.geometry("950x720")
            self.window.configure(bg='#f0f0f0')
            self.is_root = True
        else:
            self.window = tk.Toplevel(master)
            self.window.title("AI Agent 配置")
            self.window.geometry("950x720")
            self.window.configure(bg='#f0f0f0')
            self.is_root = False
        
        self._create_ui()
        
        if not config_exists():
            messagebox.showinfo(
                "首次启动",
                "欢迎使用 AI Agent！\n\n"
                "请配置你的 AI 服务信息。\n"
                "至少需要配置一个可用的 AI 后端才能正常使用。"
            )
        
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)
        
        # 启动后自动检测模型
        self.window.after(500, self._auto_check_model)
    
    def _create_ui(self):
        """创建界面 - 左右两栏布局"""
        main_frame = ttk.Frame(self.window, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 标题
        title_frame = ttk.Frame(main_frame)
        title_frame.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(title_frame, text="AI Agent 配置", font=("Microsoft YaHei", 16, "bold")).pack(side=tk.LEFT)
        ttk.Label(title_frame, text="配置后保存即可生效", font=("Microsoft YaHei", 10)).pack(side=tk.LEFT, padx=10)
        
        # 提示
        note_frame = ttk.Frame(main_frame)
        note_frame.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(note_frame, text="[提示] TokenHub 支持任意 OpenAI 兼容接口（如 OpenAI、DeepSeek、vLLM 等），填入对应 URL 和 Key 即可。", 
                  foreground="#666666").pack(anchor='w')
        
        # ============================================================
        # 左右两栏：使用 PanedWindow
        # ============================================================
        paned = ttk.PanedWindow(main_frame, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        # ---- 左栏：AI 后端配置 ----
        left_frame = ttk.Frame(paned)
        paned.add(left_frame, weight=1)
        
        left_canvas = tk.Canvas(left_frame, bg='#f0f0f0', highlightthickness=0)
        left_scrollbar = ttk.Scrollbar(left_frame, orient="vertical", command=left_canvas.yview)
        left_scrollable = ttk.Frame(left_canvas)
        
        left_scrollable.bind("<Configure>", lambda e: left_canvas.configure(scrollregion=left_canvas.bbox("all")))
        left_canvas.create_window((0, 0), window=left_scrollable, anchor="nw")
        left_canvas.configure(yscrollcommand=left_scrollbar.set)
        
        left_canvas.pack(side="left", fill="both", expand=True)
        left_scrollbar.pack(side="right", fill="y")
        
        def _left_mousewheel(event):
            left_canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        left_canvas.bind_all("<MouseWheel>", _left_mousewheel)
        
        # 左栏内容
        self._add_section(left_scrollable, "Ollama (本地模型)")
        self._add_entry(left_scrollable, "URL", "ollama_url", self.config["ollama"].get("url", ""))
        self._add_ollama_model_combobox(left_scrollable, "模型", "ollama_model", self.config["ollama"].get("model", "gemma4:e4b"))
        self._add_entry(left_scrollable, "最大Token", "ollama_max_tokens", self.config["ollama"].get("max_tokens", 8192))
        
        self._add_section(left_scrollable, "通义千问 (DashScope)")
        self._add_entry(left_scrollable, "API Key", "dashscope_api_key", self.config["dashscope"].get("api_key", ""), is_secret=True)
        self._add_entry(left_scrollable, "模型", "dashscope_model", self.config["dashscope"].get("model", ""))
        self._add_entry(left_scrollable, "最大Token", "dashscope_max_tokens", self.config["dashscope"].get("max_tokens", 8192))
        
        self._add_section(left_scrollable, "DeepSeek")
        self._add_entry(left_scrollable, "API Key", "deepseek_api_key", self.config["deepseek"].get("api_key", ""), is_secret=True)
        self._add_entry(left_scrollable, "模型", "deepseek_model", self.config["deepseek"].get("model", ""))
        self._add_entry(left_scrollable, "最大Token", "deepseek_max_tokens", self.config["deepseek"].get("max_tokens", 8192))
        
        self._add_section(left_scrollable, "智谱 AI (GLM)")
        self._add_entry(left_scrollable, "API Key", "zhipu_api_key", self.config["zhipu"].get("api_key", ""), is_secret=True)
        self._add_entry(left_scrollable, "模型", "zhipu_model", self.config["zhipu"].get("model", ""))
        self._add_entry(left_scrollable, "URL", "zhipu_url", self.config["zhipu"].get("url", ""))
        self._add_entry(left_scrollable, "最大Token", "zhipu_max_tokens", self.config["zhipu"].get("max_tokens", 8192))
        
        self._add_section(left_scrollable, "TokenHub / OpenAI 兼容接口")
        self._add_entry(left_scrollable, "API Key", "tokenhub_api_key", self.config["tokenhub"].get("api_key", ""), is_secret=True)
        self._add_entry(left_scrollable, "模型", "tokenhub_model", self.config["tokenhub"].get("model", ""))
        self._add_entry(left_scrollable, "Base URL", "tokenhub_base_url", self.config["tokenhub"].get("base_url", ""))
        self._add_entry(left_scrollable, "最大Token", "tokenhub_max_tokens", self.config["tokenhub"].get("max_tokens", 8192))
        
        # ---- 右栏：通用配置 + 语义检索 ----
        right_frame = ttk.Frame(paned)
        paned.add(right_frame, weight=1)
        
        right_canvas = tk.Canvas(right_frame, bg='#f0f0f0', highlightthickness=0)
        right_scrollbar = ttk.Scrollbar(right_frame, orient="vertical", command=right_canvas.yview)
        right_scrollable = ttk.Frame(right_canvas)
        
        right_scrollable.bind("<Configure>", lambda e: right_canvas.configure(scrollregion=right_canvas.bbox("all")))
        right_canvas.create_window((0, 0), window=right_scrollable, anchor="nw")
        right_canvas.configure(yscrollcommand=right_scrollbar.set)
        
        right_canvas.pack(side="left", fill="both", expand=True)
        right_scrollbar.pack(side="right", fill="y")
        
        def _right_mousewheel(event):
            right_canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        # right_canvas.bind_all("<MouseWheel>", _right_mousewheel)
        
        # 右栏内容
        self._add_section(right_scrollable, "通用设置")
        self._add_entry(right_scrollable, "Temperature", "temperature", self.config["general"].get("temperature", 0.3))
        self._add_entry(right_scrollable, "超时(秒)", "timeout", self.config["general"].get("timeout", 120))
        self._add_entry(right_scrollable, "空闲触发心跳(秒)", "idle_threshold", self.config["general"].get("idle_threshold", 60))
        self._add_entry(right_scrollable, "心跳间隔(秒)", "learning_interval", self.config["general"].get("learning_interval", 300))
        
        self._add_combobox(right_scrollable, "主模型后端", "main_backend", 
                   self.config["general"].get("main_backend", "ollama"),
                   ["ollama", "dashscope", "deepseek", "zhipu", "tokenhub"])
        self._add_combobox(right_scrollable, "辅助模型后端", "assistant_backend",
                   self.config["general"].get("assistant_backend", "zhipu"),
                   ["ollama", "dashscope", "deepseek", "zhipu", "tokenhub"])
        
        # 语义检索配置
        self._add_section(right_scrollable, "语义检索配置")
        
        # 模型选择 + 按钮（同一行）
        row1 = ttk.Frame(right_scrollable)
        row1.pack(fill=tk.X, pady=3)
        
        ttk.Label(row1, text="向量模型", width=15, anchor='w').pack(side=tk.LEFT, padx=(0, 5))
        
        var = tk.StringVar(
            value=self.config["general"].get("embedding_model", "paraphrase-multilingual-MiniLM-L12-v2")
        )
        
        models = [
            "paraphrase-multilingual-MiniLM-L12-v2",
            "BAAI/bge-small-zh-v1.5",
            "BAAI/bge-base-zh-v1.5",
            "sentence-transformers/all-MiniLM-L6-v2",
        ]
        
        combobox = ttk.Combobox(row1, textvariable=var, width=30, state="readonly")
        combobox['values'] = models
        combobox.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.entries["embedding_model"] = var
                
        # 第二行：按钮（缩进对齐）
        row2 = ttk.Frame(right_scrollable)
        row2.pack(fill=tk.X, pady=(2, 5))

        # 缩进占位，与上面的标签对齐
        ttk.Label(row2, width=15).pack(side=tk.LEFT, padx=(0, 5))

        btn_frame = ttk.Frame(row2)
        btn_frame.pack(side=tk.LEFT, padx=(5, 0))
        ttk.Button(btn_frame, text="检测", command=self._check_embedding_model).pack(side=tk.LEFT, padx=1)
        ttk.Button(btn_frame, text="下载", command=self._download_embedding_model).pack(side=tk.LEFT, padx=1)
        
        # 状态显示 + 进度条
        status_frame = ttk.Frame(right_scrollable)
        status_frame.pack(fill=tk.X, pady=(2, 3))
        
        self.model_status_var = tk.StringVar(value="就绪")
        ttk.Label(status_frame, textvariable=self.model_status_var, 
                 foreground="#666666").pack(anchor='w', padx=(5, 0))
        
        # 进度条（下载时显示）
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(
            right_scrollable, 
            variable=self.progress_var,
            maximum=100,
            length=200,
            mode='determinate'
        )
        self.progress_bar.pack(fill=tk.X, padx=(5, 0), pady=(0, 5))
        self.progress_bar.pack_forget()  # 默认隐藏
        
        # 其他参数
        self._add_entry(right_scrollable, "最小相似度", "embedding_min_similarity", 
                        self.config["general"].get("embedding_min_similarity", 0.25))
        self._add_entry(right_scrollable, "检索数量(TopK)", "embedding_top_k", 
                        self.config["general"].get("embedding_top_k", 5))
        
        # ---- 底部按钮区域 ----
        btn_bottom = ttk.Frame(main_frame)
        btn_bottom.pack(fill=tk.X, pady=(5, 0))
        
        ttk.Button(btn_bottom, text="保存配置", command=self._save_and_close, 
                   style="Accent.TButton").pack(side=tk.RIGHT, padx=5)
        ttk.Button(btn_bottom, text="保存并测试连接", command=self._save_and_test).pack(side=tk.RIGHT, padx=5)
        ttk.Button(btn_bottom, text="恢复默认", command=self._reset_default).pack(side=tk.RIGHT, padx=5)
        ttk.Button(btn_bottom, text="取消", command=self._on_close).pack(side=tk.RIGHT, padx=5)
        
        # ---- 底部状态栏 ----
        self.status_var = tk.StringVar(value="就绪")
        status_bar = ttk.Label(main_frame, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        status_bar.pack(fill=tk.X, pady=(5, 0))
    
    # =========================================================================
    # 自动检测模型（启动时调用）
    # =========================================================================
    
    def _auto_check_model(self):
        """启动时自动检测当前配置的模型"""
        model_name = self.entries["embedding_model"].get()
        if not model_name:
            return
        
        self.model_status_var.set("正在检测模型...")
        
        def check():
            result = check_embedding_model(model_name)  # ← 只传一个参数
            if result["available"]:
                self.window.after(0, lambda: self.model_status_var.set(
                    f"模型可用 (维度: {result['dimension']})"
                ))
                self.window.after(0, lambda: self.status_var.set("模型就绪"))
            else:
                self.window.after(0, lambda: self.model_status_var.set(
                    "模型未下载，点击「下载模型」安装"
                ))
                self.window.after(0, lambda: self.status_var.set("模型未就绪"))
        
        threading.Thread(target=check, daemon=True).start()
    
    def _prompt_download(self, model_name):
        """提示用户下载模型"""
        if messagebox.askyesno("模型不存在", 
            f"当前配置的模型 {model_name} 未找到，是否立即下载？\n\n"
            "下载可能需要几分钟，使用国内镜像加速。"):
            self._download_embedding_model()
    
    # =========================================================================
    # 手动检测和下载
    # =========================================================================
    
    def _check_embedding_model(self):
        """手动检测语义模型"""
        model_name = self.entries["embedding_model"].get()
        if not model_name:
            self.model_status_var.set("请选择模型")
            return
        
        self.model_status_var.set("正在检测...")
        self.status_var.set("检测中...")
        self.progress_bar.pack_forget()
        
        def check():
            result = check_embedding_model(model_name)  # ← 只传一个参数
            
            if result["available"]:
                self.window.after(0, lambda: self.model_status_var.set(
                    f"模型可用 (维度: {result['dimension']})"
                ))
                self.window.after(0, lambda: self.status_var.set("模型就绪"))
                self.window.after(0, lambda: self.progress_bar.pack_forget())
            else:
                self.window.after(0, lambda: self.model_status_var.set(
                    f"检测失败: {result.get('error', '未知错误')[:50]}"
                ))
                self.window.after(0, lambda: self.status_var.set("检测失败"))
                self.window.after(0, lambda: self.progress_bar.pack_forget())
        
        threading.Thread(target=check, daemon=True).start()
    
    def _download_embedding_model(self):
        """下载语义模型（带进度条）"""
        model_name = self.entries["embedding_model"].get()
        if not model_name:
            self.model_status_var.set("请选择模型")
            return
        
        result = check_embedding_model(model_name)
        if result["available"]:
            if not messagebox.askyesno("已存在", 
                f"模型 {model_name} 已存在，是否重新下载？"):
                return
        
        if not messagebox.askyesno("确认下载", 
            f"将下载模型: {model_name}\n\n"
            "首次下载可能需要几分钟，请耐心等待。\n"
            "使用国内镜像加速 (hf-mirror.com)"):
            return
        
        self.progress_var.set(0)
        self.progress_bar.pack(fill=tk.X, padx=(5, 0), pady=(0, 5))
        self.progress_bar.config(mode='indeterminate')
        self.progress_bar.start(10)
        self.model_status_var.set("正在下载，请等待...")
        self.status_var.set("下载中...")
        
        def download():
            def update_progress(percent, msg):
                self.window.after(0, lambda: self.model_status_var.set(msg))
                self.window.after(0, lambda: self.status_var.set(msg[:30]))
            
            success = download_embedding_model(model_name, update_progress)
            
            self.window.after(0, lambda: self.progress_bar.stop())
            self.window.after(0, lambda: self.progress_bar.pack_forget())
            self.window.after(0, lambda: self.progress_bar.config(mode='determinate'))
            
            if success:
                self.window.after(0, lambda: self.model_status_var.set("验证模型..."))
                
                def verify():
                    try:
                        os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
                        from sentence_transformers import SentenceTransformer
                        model = SentenceTransformer(model_name)
                        dim = model.get_sentence_embedding_dimension()
                        model.encode("测试", normalize_embeddings=True)
                        
                        self.window.after(0, lambda: self.model_status_var.set(f"模型可用 (维度: {dim})"))
                        self.window.after(0, lambda: self.status_var.set("模型就绪"))
                        self.window.after(0, lambda: messagebox.showinfo("成功", f"模型 {model_name} 下载完成！"))
                    except Exception as e:
                        self.window.after(0, lambda: self.model_status_var.set(f"验证失败: {str(e)[:50]}"))
                        self.window.after(0, lambda: self.status_var.set("验证失败"))
                        self.window.after(0, lambda: messagebox.showerror("验证失败", f"模型下载完成但验证失败:\n{str(e)}"))
                
                threading.Thread(target=verify, daemon=True).start()
            else:
                self.window.after(0, lambda: messagebox.showerror("失败", "下载失败，请检查网络连接。"))
                self.window.after(0, lambda: self.status_var.set("下载失败"))
        
        threading.Thread(target=download, daemon=True).start()
    
    # =========================================================================
    # UI 辅助方法
    # =========================================================================
    
    def _add_section(self, parent, title):
        ttk.Separator(parent, orient='horizontal').pack(fill=tk.X, pady=(12, 6))
        ttk.Label(parent, text=title, font=("Microsoft YaHei", 11, "bold")).pack(anchor='w', pady=(0, 3))
    
    def _add_entry(self, parent, label, key, default, is_secret=False):
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.X, pady=2)
        lbl = ttk.Label(frame, text=label, width=15, anchor='w')
        lbl.pack(side=tk.LEFT, padx=(0, 5))
        
        var = tk.StringVar(value=str(default))
        entry = ttk.Entry(frame, textvariable=var)
        if is_secret:
            entry.config(show="*")
            def toggle_show():
                if entry.cget('show') == '*':
                    entry.config(show='')
                    btn.config(text='隐藏')
                else:
                    entry.config(show='*')
                    btn.config(text='显示')
            btn = ttk.Button(frame, text='显示', width=4, command=toggle_show)
            btn.pack(side=tk.LEFT, padx=(2, 0))
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.entries[key] = var
    
    def _add_combobox(self, parent, label, key, default, options):
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.X, pady=2)
        lbl = ttk.Label(frame, text=label, width=15, anchor='w')
        lbl.pack(side=tk.LEFT, padx=(0, 5))
        
        var = tk.StringVar(value=str(default))
        combobox = ttk.Combobox(frame, textvariable=var, state="readonly")
        combobox['values'] = options
        combobox.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.entries[key] = var

    def _add_ollama_model_combobox(self, parent, label, key, default):
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.X, pady=2)
        lbl = ttk.Label(frame, text=label, width=15, anchor='w')
        lbl.pack(side=tk.LEFT, padx=(0, 5))
        
        var = tk.StringVar(value=str(default))
        models = get_ollama_models()
        if not models:
            models = ["gemma4:e4b", "qwen3:8b", "qwen2.5:7b", "qwen3.5:4b", "llama3:8b"]
        
        combobox = ttk.Combobox(frame, textvariable=var, state="readonly")
        combobox['values'] = models
        combobox.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.entries[key] = var
    
    # =========================================================================
    # 配置保存和验证
    # =========================================================================
    
    def _collect_config(self) -> dict:
        config = {
            "ollama": {
                "url": self.entries["ollama_url"].get(),
                "model": self.entries["ollama_model"].get(),
                "max_tokens": int(self.entries["ollama_max_tokens"].get() or 8192)
            },
            "dashscope": {
                "api_key": self.entries["dashscope_api_key"].get(),
                "model": self.entries["dashscope_model"].get(),
                "max_tokens": int(self.entries["dashscope_max_tokens"].get() or 8192)
            },
            "deepseek": {
                "api_key": self.entries["deepseek_api_key"].get(),
                "model": self.entries["deepseek_model"].get(),
                "max_tokens": int(self.entries["deepseek_max_tokens"].get() or 8192)
            },
            "zhipu": {
                "api_key": self.entries["zhipu_api_key"].get(),
                "model": self.entries["zhipu_model"].get(),
                "url": self.entries["zhipu_url"].get(),
                "max_tokens": int(self.entries["zhipu_max_tokens"].get() or 8192)
            },
            "tokenhub": {
                "api_key": self.entries["tokenhub_api_key"].get(),
                "model": self.entries["tokenhub_model"].get(),
                "base_url": self.entries["tokenhub_base_url"].get(),
                "max_tokens": int(self.entries["tokenhub_max_tokens"].get() or 8192)
            },
            "general": {
                "temperature": float(self.entries["temperature"].get() or 0.3),
                "timeout": int(self.entries["timeout"].get() or 120),
                "main_backend": self.entries["main_backend"].get(),
                "assistant_backend": self.entries["assistant_backend"].get(),
                "idle_threshold": int(self.entries["idle_threshold"].get() or 60),
                "learning_interval": int(self.entries["learning_interval"].get() or 300),
                "embedding_model": self.entries["embedding_model"].get(),
                "embedding_min_similarity": float(self.entries["embedding_min_similarity"].get() or 0.25),
                "embedding_top_k": int(self.entries["embedding_top_k"].get() or 5),
            }
        }
        return config
    
    def _validate_config(self, config: dict) -> tuple:
        has_valid = False
        backends = {
            "ollama": "Ollama (本地)",
            "dashscope": "通义千问",
            "deepseek": "DeepSeek",
            "zhipu": "智谱 AI",
            "tokenhub": "TokenHub"
        }
        
        for key, name in backends.items():
            if key == "ollama":
                if config[key].get("url") and config[key].get("model"):
                    has_valid = True
            else:
                if config[key].get("api_key") and config[key].get("model"):
                    has_valid = True
        
        if not has_valid:
            return False, "至少需要配置一个可用的 AI 后端（API Key 和模型名称必须填写）"
        
        main = config["general"]["main_backend"]
        if main != "ollama":
            if not config.get(main, {}).get("api_key"):
                return False, f"主后端 '{main}' 的 API Key 未配置"
        
        return True, ""
    
    def _save_config(self) -> bool:
        config = self._collect_config()
        valid, msg = self._validate_config(config)
        if not valid:
            messagebox.showerror("配置错误", msg)
            return False
        
        if save_config(config):
            self.status_var.set("配置已保存")
            return True
        else:
            messagebox.showerror("错误", "保存配置失败")
            return False
    
    def _save_and_close(self):
        if self._save_config():
            if self.on_save_callback:
                self.on_save_callback()
            self._close_window()
    
    def _save_and_test(self):
        if not self._save_config():
            return
        
        self.status_var.set("正在测试连接...")
        self.window.update()
        
        try:
            from ai_client import reload_config, AIClient
            reload_config()
            client = AIClient()
            if client.test_connection():
                messagebox.showinfo("成功", "主模型连接测试成功！")
                self.status_var.set("连接测试通过")
            else:
                messagebox.showwarning("警告", "主模型连接失败，请检查配置")
                self.status_var.set("连接测试失败")
        except Exception as e:
            messagebox.showerror("错误", f"测试连接时出错: {e}")
            self.status_var.set(f"测试错误: {e}")
    
    def _reset_default(self):
        if messagebox.askyesno("确认", "将恢复所有配置为默认值，确定吗？"):
            for key, var in self.entries.items():
                if key.startswith("ollama_"):
                    field = key[7:]
                    default = DEFAULT_CONFIG["ollama"].get(field, "")
                elif key.startswith("dashscope_"):
                    field = key[10:]
                    default = DEFAULT_CONFIG["dashscope"].get(field, "")
                elif key.startswith("deepseek_"):
                    field = key[9:]
                    default = DEFAULT_CONFIG["deepseek"].get(field, "")
                elif key.startswith("zhipu_"):
                    field = key[6:]
                    default = DEFAULT_CONFIG["zhipu"].get(field, "")
                elif key.startswith("tokenhub_"):
                    field = key[9:]
                    default = DEFAULT_CONFIG["tokenhub"].get(field, "")
                elif key in ['embedding_model', 'embedding_min_similarity', 'embedding_top_k']:
                    default = DEFAULT_CONFIG["general"].get(key, "")
                else:
                    default = DEFAULT_CONFIG["general"].get(key, "")
                var.set(str(default))
            self.status_var.set("已恢复默认值")
    
    def _on_close(self):
        self._close_window()
    
    def _close_window(self):
        self.window.unbind_all("<MouseWheel>")
        if self.is_root:
            self.window.quit()
            self.window.destroy()
        else:
            self.window.destroy()
    
    def run(self):
        if self.is_root:
            self.window.mainloop()
    
    def show(self):
        self.window.deiconify()
        self.window.lift()


# ============================================================================
# 外部调用接口
# ============================================================================

def show_config_window(parent=None, on_save=None):
    win = ConfigWindow(parent, on_save)
    win.show()
    return win

def run_config_window():
    win = ConfigWindow()
    win.run()

def ensure_config():
    if config_exists():
        return True
    win = ConfigWindow()
    win.run()
    return config_exists()

def get_config() -> dict:
    return load_config()


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        if sys.argv[1] == "--ensure":
            if ensure_config():
                print("配置已就绪")
                sys.exit(0)
            else:
                print("配置未完成")
                sys.exit(1)
        elif sys.argv[1] == "--show":
            run_config_window()
        elif sys.argv[1] == "--help":
            print("用法:")
            print("  python config_manager.py         打开配置窗口")
            print("  python config_manager.py --ensure 确保配置存在（不存在则弹出）")
            print("  python config_manager.py --show   显示配置窗口")
            print("  python config_manager.py --help   显示帮助")
        else:
            run_config_window()
    else:
        run_config_window()
