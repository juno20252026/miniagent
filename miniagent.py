#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI Agent v9.9 - 双模型协作版本
核心变化：
1. 双屏设计：主屏显示对话，副屏显示中间过程（回忆、代码执行、计划、工具调用）
2. 双模型协作：主模型负责指令交互，辅助模型处理子任务
3. 取消RESPONSE指令，所有回复直接在主屏显示
4. 保留SEARCH指令，代码执行使用 [PYTHON] 标记
5. 所有指令为可选项，AI可自由选择输出格式
6. 增强JSON解析：从AI返回中提取指令块，而非强制要求
7. 指令块解析后先保存，执行后再修改指令块返回
"""
import sys
import locale
import os

# ===== 强制设置编码（兼容所有情况） =====
if sys.platform == 'win32':
    try:
        # 检查 sys.stdout 是否存在且可写
        if sys.stdout is not None and hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        if sys.stderr is not None and hasattr(sys.stderr, 'reconfigure'):
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    
    try:
        locale.setlocale(locale.LC_ALL, 'zh_CN.UTF-8')
    except:
        try:
            locale.setlocale(locale.LC_ALL, 'Chinese_China.936')
        except:
            pass



import json
import subprocess
import threading
import time
import re


import tempfile
import weakref
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
import tkinter as tk
from tkinter import scrolledtext, messagebox, ttk
from ai_client import (AIClient, CollaborativeAIClient, IDLE_THRESHOLD, 
                       LEARNING_INTERVAL, MAX_LEARNING_ROUNDS,
                       create_collaborative_client)
import warnings
warnings.filterwarnings('ignore', category=DeprecationWarning, module='sqlite3')
import sqlite3
from json_parser import JSONParser
from collections import deque

from mission_manager import (
    MissionManager, 
    get_manager, 
    init_manager, 
    shutdown_manager,
    create_task_from_ai
)

from extension_manager import ExtensionManager
from prompts import CORE_PROMPT, INTROSPECTION_CORE, PromptAssembler
from knowledge_base import KnowledgeBaseInterface
from config_manager import ensure_config, show_config_window, get_config
from semantic_retriever import SemanticRetriever

# ============================================================================
# 配置
# ============================================================================

CODE_TIMEOUT = 30
MAX_RETRIES = 3
MAX_HISTORY = 2500
MAX_CONTEXT_TOKENS = 7000
MAX_SEARCH_RESULTS = 5
MAX_TOOLS_INJECTION = 10

# 协作模式配置
ENABLE_ASSISTANT = True  # 是否启用辅助模型
MAX_ASSISTANT_RETRIES = 2


# ============================================================================
# Token计数器
# ============================================================================

class TokenCounter:
    _cache: Dict[str, int] = {}
    
    @staticmethod
    def estimate(text: str) -> int:
        if not text:
            return 0
        # 使用缓存
        cache_key = text[:200] + str(len(text))
        if cache_key in TokenCounter._cache:
            return TokenCounter._cache[cache_key]
        
        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
        english_chars = len(text) - chinese_chars
        result = int(chinese_chars / 1.5 + english_chars / 4)
        TokenCounter._cache[cache_key] = result
        return result
    
    @staticmethod
    def truncate(text: str, max_tokens: int) -> str:
        if TokenCounter.estimate(text) <= max_tokens:
            return text
        ratio = max_tokens / TokenCounter.estimate(text)
        new_len = int(len(text) * ratio)
        return text[:new_len] + "...(已截断)"


# ============================================================================
# 数据库
# ============================================================================

class Database:
    def __init__(self, db_path: str = "agent_memory.db"):
        self.db_path = db_path
        self._init_db()
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            conn.execute('PRAGMA journal_mode=WAL;')
        print("数据库初始化完成")
    
    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    learned INTEGER DEFAULT 0,
                    search_count INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tools (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    description TEXT NOT NULL,
                    code TEXT NOT NULL,
                    keywords TEXT,
                    state_in TEXT,
                    state_out TEXT,
                    usage_count INTEGER DEFAULT 0,
                    source TEXT DEFAULT 'plan',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    last_used_at DATETIME,
                    command_pattern TEXT,
                    is_extension INTEGER DEFAULT 0
                )
            """)
    
    # ========== 通用查询辅助 ==========
    
    def _query(self, sql: str, params: tuple = (),
               fetch_one: bool = False, return_dict: bool = True) -> Any:
        """通用查询方法，减少重复代码"""
        with sqlite3.connect(self.db_path) as conn:
            if return_dict:
                conn.row_factory = sqlite3.Row
            cursor = conn.execute(sql, params)
            if fetch_one:
                row = cursor.fetchone()
                return dict(row) if row and return_dict else row
            rows = cursor.fetchall()
            return [dict(row) for row in rows] if return_dict else rows
    
    def _execute(self, sql: str, params: tuple = ()):
        """通用执行方法"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(sql, params)
            conn.commit()
    
    # ========== Conversations 操作 ==========
    
    def add_message(self, role: str, content: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO conversations (role, content) VALUES (?, ?)",
                (role, content)
            )
            conn.execute("""
                DELETE FROM conversations 
                WHERE id NOT IN (
                    SELECT id FROM conversations 
                    ORDER BY learned ASC, timestamp DESC 
                    LIMIT ?
                )
            """, (MAX_HISTORY,))
    
    def get_recent_history(self, limit: int = 20) -> List[Dict]:
        rows = self._query(
            "SELECT role, content, timestamp FROM conversations ORDER BY timestamp DESC LIMIT ?",
            (limit,)
        )
        return list(reversed(rows))
    
    def get_unlearned_messages(self, limit: int = 10) -> List[Dict]:
        return self._query(
            "SELECT id, role, content, timestamp FROM conversations WHERE content NOT LIKE '[心跳]%' ORDER BY learned ASC, id DESC LIMIT ?",
            (limit,)
        )
    
    def mark_messages_learned(self, message_ids: List[int]):
        if not message_ids:
            return
        placeholders = ",".join("?" * len(message_ids))
        self._execute(
            f"UPDATE conversations SET learned = learned + 1 WHERE id IN ({placeholders})",
            tuple(message_ids)
        )
    
    def clear_conversations(self):
        self._execute("DELETE FROM conversations")
    
    def search_conversations(self, query: str, limit: int = MAX_SEARCH_RESULTS) -> List[Dict]:
        keywords = re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z]{2,}', query)
        if not keywords:
            return []
        fetch_limit = limit * 2
        conditions = " OR ".join(["content LIKE ?"] * min(len(keywords), 3))
        params = [f"%{kw}%" for kw in keywords[:3]]
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            rows = cursor.execute(
                f"SELECT id, role, content, timestamp FROM conversations WHERE {conditions} ORDER BY search_count ASC, timestamp DESC LIMIT ?",
                tuple(params + [fetch_limit])
            ).fetchall()
            # 计算每条记录匹配了多少个关键词
            scored = []
            for row in rows:
                content = row[2]
                match_count = sum(1 for kw in keywords[:3] if kw in content)
                scored.append((match_count, row))
            
            # 按匹配数降序排列，取前 limit 条
            scored.sort(key=lambda x: x[0], reverse=True)
            rows = [row for _, row in scored[:limit]]
            
            # 更新 search_count
            if rows:
                ids = [row[0] for row in rows]
                placeholders = ",".join(["?"] * len(ids))
                cursor.execute(
                    f"UPDATE conversations SET search_count = search_count + 1 WHERE id IN ({placeholders})",  # 改为 id
                    tuple(ids)
                )
                conn.commit()
            
            return [{
                "type": "conversation", 
                "role": row[1],      # 第2列是 role
                "content": TokenCounter.truncate(row[2], 300),  # 第3列是 content
                "timestamp": row[3]  # 第4列是 timestamp
            } for row in rows]

# ============================================================================
# 沙盒执行器
# ============================================================================

class Sandbox:
    def __init__(self):
        # 获取脚本所在目录的绝对路径
        script_dir = os.path.dirname(os.path.abspath(__file__))
        self.project_root = Path(script_dir)           # ← 新增
        self.sandbox_dir = self.project_root / "sandbox_temp"
        self.sandbox_dir.mkdir(parents=True, exist_ok=True)
        self._global_state: Dict[str, any] = {}
        self._state_lock = threading.RLock()
        print("沙盒环境初始化完成")
    
    def _safe_check(self, code: str) -> Tuple[bool, str]:
        dangerous = [
            # (r'os\.system', "禁止 os.system"),
            # (r'subprocess\.', "禁止 subprocess"),
            (r'eval\s*\(', "禁止 eval"),
            # (r'exec\s*\(', "禁止 exec"),
            # (r'__import__', "禁止动态导入"),
            (r'globals\s*\(', "禁止访问全局变量"),  
            (r'\blocals\s*\(', "禁止访问局部变量"),  
            (r'input\s*\(', "禁止 input()"),
        ]
        for pattern, msg in dangerous:
            if re.search(pattern, code, re.IGNORECASE):
                return False, msg
        return True, ""
    
    def get_state_keys(self) -> List[str]:
        with self._state_lock:
            return list(self._global_state.keys())
    
    def get_state(self, key: str) -> any:
        with self._state_lock:
            return self._global_state.get(key)
    
    def set_state(self, key: str, value: any):
        with self._state_lock:
            if isinstance(value, (str, int, float, bool, list, dict, tuple, type(None))):
                self._global_state[key] = value

    
    def execute(self, code: str, timeout: int = CODE_TIMEOUT, **kwargs) -> Tuple[str, str, bool]:
        safe, msg = self._safe_check(code)
        if not safe:
            return "", msg, False
        
        with self._state_lock:
            state_snapshot = self._global_state.copy()
        for key, value in kwargs.items():
            state_snapshot[key] = value

           
        # ===== 自动调用 run()，传递状态变量 =====
        if re.search(r'^def\s+run\s*\(', code, re.MULTILINE):
            # 构建 kwargs
            kwargs_parts = []
            for k, v in state_snapshot.items():
                try:
                    json.dumps(v)
                    kwargs_parts.append(f"{k}={repr(v)}")
                except:
                    pass
            
            if kwargs_parts:
                param_str = ", ".join(kwargs_parts)
                code = code + f'\n\n# 自动调用 run()\n_result = run({param_str})\nif _result is not None:\n    print(_result)'
            else:
                code = code + '\n\n# 自动调用 run()\n_result = run()\nif _result is not None:\n    print(_result)'
        # ===== 结束 =====
        
        wrapper = f'''
import json
import sys
import io 
import math
import random
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

_state = {json.dumps(state_snapshot, ensure_ascii=True)}

def get_state(key, default=None):
    return _state.get(key, default)

def set_state(key, value):
    try:
        if isinstance(value, bytes):
            value = value.decode('utf-8', errors='replace')

        # ===== 检查是否可 JSON 序列化 =====
        try:
            json.dumps(value)
        except (TypeError, ValueError) as e:
            raise TypeError(f"无法保存变量 '{{key}}': {{type(value).__name__}} 不支持 JSON 序列化。"
                           f"请将数据转换为 dict/list/str/int/float/bool 后再保存。")
        # =================================
        
        _state[key] = value
        print("__STATE_UPDATE__:" + json.dumps({{key: value}}, ensure_ascii=False), file=sys.stderr)
    except Exception as e:
        print(f"set_state error: {{e}}", file=sys.stderr)

def list_state():
    return list(_state.keys())

# 用户代码开始
{code}
# 用户代码结束

print("__STATE_FINAL__", file=sys.stderr)
'''
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', dir=self.sandbox_dir,
                                         delete=False, encoding='utf-8') as f:
            temp_file = f.name
            f.write(wrapper)
        
        try:
            result = subprocess.run(
                [sys.executable, temp_file],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(self.project_root),
                encoding='utf-8',
                errors='replace'
            )
            stdout, stderr = result.stdout, result.stderr
            
            if stderr is None:
                stderr = ""
                
            for line in stdout.split('\n'):
                if line.startswith('__STATE_UPDATE__:'):
                    try:
                        updates = json.loads(line[len('__STATE_UPDATE__:'):])
                        for k, v in updates.items():
                            self.set_state(k, v)
                    except:
                        pass

            for line in stderr.split('\n'):
                if line.startswith('__STATE_UPDATE__:'):
                    try:
                        updates = json.loads(line[len('__STATE_UPDATE__:'):])
                        for k, v in updates.items():
                            self.set_state(k, v)
                    except:
                        pass
            
            clean_lines = [l for l in stdout.split('\n') if not l.startswith('__STATE_UPDATE__:')]
            return '\n'.join(clean_lines).strip(), stderr.strip(), result.returncode == 0
            
        except subprocess.TimeoutExpired:
            return "", f"超时(>{timeout}秒)", False
        except Exception as e:
            error_msg = str(e)
            print(f"ERROR: {error_msg}")  # 关键：打印到 stdout
            return f"ERROR: {error_msg}", error_msg, False
        finally:
            try:
                os.unlink(temp_file)
            except:
                pass
    
    def force_cleanup(self):
        """强制清理所有临时文件和状态"""
        import gc
        import shutil
        
        try:
            if self.sandbox_dir.exists():
                shutil.rmtree(self.sandbox_dir)
                self.sandbox_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"清理沙盒目录失败: {e}")
        
        with self._state_lock:
            self._global_state.clear()
        
        gc.collect()
        gc.collect()

# ============================================================================
# 心跳机制
# ============================================================================

class Introspector:
    INTROSPECTION_SYSTEM = INTROSPECTION_CORE
    
    INTROSPECTION_PROMPT = """
{history}

开始你的心跳。
"""

    def __init__(self, db: Database, ai_client: AIClient, logger=None, output_callback=None, process_callback=None, agent=None):
        self.db = db
        self.ai = ai_client 
        self.logger = logger or (lambda x: None)
        self.output_callback = output_callback or (lambda x, append=False: None)
        self.process_callback = process_callback or (lambda x: None)
        self.agent = agent
        self._is_mission_phase = True
        print("心跳机制初始化启动完成")
      
    def learn_from_history(self, rounds: int = MAX_LEARNING_ROUNDS):
        source = "heartbeat"
        if self.agent and hasattr(self.agent, '_introspection_source'):
            source = self.agent._introspection_source
        
        # ===== 注入时间到心跳 =====
        now = datetime.now()
        weekdays = ["一", "二", "三", "四", "五", "六", "日"]
        time_info = f"\n## 当前时间\n{now.strftime('%Y年%m月%d日 %H:%M:%S')} 星期{weekdays[now.weekday()]}\n"
        messages = [{"role": "system", "content": self.agent.prompt_assembler.get_introspection_full_prompt()+ time_info}]
        loaded = self.agent.prompt_assembler.get_loaded_content()
        if loaded:
            messages.append({"role": "system", "content": loaded})
        if self.agent:
            memory = self.agent.get_memory_summary()
            if memory:
                messages.append({"role": "system", "content": memory})
                
        if source == "mission" and self._is_mission_phase:
            self._is_mission_phase = not self._is_mission_phase
            task_info = ""
            if hasattr(self.agent, '_current_mission_task_id'):
                task_id = self.agent._current_mission_task_id
                task = get_manager().get_task(task_id)
                if task:
                    task_info = f"\n\n## 正在执行任务\n- ID: {task.id}\n- 名称: {task.name}\n- 描述: {task.description}\n"
                    task_info += "\n执行完成后必须调用更新状态并删除本条记忆！"

                    
            messages.append({"role": "user", "content": f"[任务管理机制]:请执行任务并更新任务状态。{task_info}"})
            self.process_callback("[任务模式] 跳过历史会话")

            response = self.ai.chat(messages)
            if not response:
                self.process_callback("[任务模式] AI无响应")
                return {"success": False, "reason": "AI无响应"}
                

            self.process_callback(f"[任务模式] 接收 {len(response)} 字符\n{response}\n")
            self.process_callback(f"[任务模式] 原始响应: {response}")

            if self.agent:
                # ===== 确保心跳模式 =====
                self.agent._heartbeat_mode = True
                self.agent._call_depth = 0  
                result = self.agent._process_response(response)
                    
                self.process_callback(f"[任务模式] 指令执行完成: {result if result else '空'}")
                return {"success": True, "executed": True, "result": result}
                
            return {"success": False, "reason": "无待执行任务", "executed": False}
        
        unlearned = self.db.get_unlearned_messages(5)
        if len(unlearned) < 5:
            self.process_callback("无新对话，跳过学习")
            return {"success": False, "summary": "无新对话", "reason": "无未学习内容"}
        
        formatted = []
        role_map = {"user": "用户", "assistant": "AI", "system": "系统"}
        for msg in unlearned:
            role = role_map.get(msg["role"], msg["role"])
            content = msg["content"][:250] + ("……" + msg["content"][-250:] if len(msg["content"]) > 500 else "")
            formatted.append(f"{role}: {content}")
                
        history_text = "\n".join(formatted)
        
        prompt = "\n\n待学习的历史会话：" +history_text
        
        self.process_callback(f"开始学习 {len(unlearned)} 条对话...")

        if self.agent:
            self.agent._heartbeat_mode = True            
            self.agent._call_depth = 0 
        
        try:
            messages.append({"role": "user", "content": prompt})
            response = self.ai.chat(messages)
            if response:
                self.process_callback(f"[心跳机制] 接收 {len(response)} 字符\n{response}\n")
                self.process_callback(f"[心跳调试] 原始响应: {response}")



            if self.agent:
                result = self.agent._process_response(response)
                if result and result != response:  # 有实际处理
                    self.process_callback(f"心跳响应处理完成")
                    return {"success": True, "executed": True, "result": result}

            self.output_callback(f"\n[心跳机制 AI 返回]\n{response}\n", append=False)
            self.process_callback("心跳响应无指令")




            if self.agent and response:

                summary = response
                self.agent.db.add_message("assistant", f"[心跳] AI在自言自语：{summary}")

            
            return {"success": False, "summary": "无指令", "reason": "无JSON指令"}
        finally:
            if self.agent:
                self.agent._heartbeat_mode = False


# ============================================================================
# AI Agent 核心
# ============================================================================

class AIAgent:
    MEMORY_FILE = Path("./short_term_memory.json")
    
    def __init__(self, status_callback=None, response_callback=None, process_callback=None):
        self.db = Database()
        self.sandbox = Sandbox()
        
        self.status_callback = status_callback or (lambda x: None)
        self.response_callback = response_callback or (lambda x, append=False: None)
        self.process_callback = process_callback or (lambda x: None)
        
        self.ai = create_collaborative_client(
            stats_callback=self.process_callback
        )
        
        self._interrupt = False
        self._search_in_this_turn = 0

        self._search_results_cache = []
        self._search_injected = False
        self.short_term_memory = []
        self._load_short_term_memory()
        self._process_lock = threading.Lock()
        
        # 自省学习器（使用主模型）
        self.introspector = Introspector(
            self.db, 
            self.ai.get_main_client(), 
            self.status_callback, 
            output_callback=self.response_callback, 
            process_callback=self.process_callback,
            agent=self  
        )
        self.last_activity = time.time()
        self.last_introspection = time.time()
        self.is_processing = False
        self._heartbeat_mode = False
        self._is_user_loop = False 
        self.act_log=deque(maxlen=20)
        self._retrieval_done = False
        
        # 检查辅助模型
        if ENABLE_ASSISTANT and self.ai.is_assistant_available():
            self.process_callback("[协作模式] 辅助模型已就绪")
        elif ENABLE_ASSISTANT:
            self.process_callback("[协作模式] 辅助模型不可用，将使用主模型处理所有任务")
        self._collab_enabled = ENABLE_ASSISTANT
        
        # ===== 扩展管理器 =====
        try:
            self.ext_manager = ExtensionManager(db_path="agent_memory.db", logger=self.process_callback)
        except Exception as e:
            self.process_callback(f"[扩展系统] 加载失败: {e}")
            self.ext_manager = None
            
        self.mission_manager = init_manager(
            trigger_heartbeat=self.do_introspection,
            logger=self.process_callback,
            sandbox=self.sandbox  
        )
        self.mission_manager.set_ai_callback(self._on_code_task_result)
        self._introspection_source = "heartbeat"

        self.prompt_assembler = PromptAssembler()
        self.kb_interface = KnowledgeBaseInterface(
            db_path="agent_memory.db",
            knowledge_dir="./knowledge_base"
        )
        # ===== 计划变量 =====
        self.current_plan_block_text = ""
        # 加载已有计划
        try:
            with open("current_plan.txt", "r", encoding='utf-8') as f:
                self.current_plan_block_text = f.read()
                if self.current_plan_block_text:
                    self.process_callback(f"[计划块] 加载了已有计划")
        except:
            pass

        # ===== 初始化语义检索器 =====
        try:
            # 从配置读取模型名称，设置到环境变量
            config = get_config()
            general = config.get("general", {})
            model_name = general.get("embedding_model", "paraphrase-multilingual-MiniLM-L12-v2")
            os.environ["EMBEDDING_MODEL"] = model_name  # ← 通过环境变量传递
            self.retriever = SemanticRetriever(
                db_path="agent_memory.db",
                logger=self.process_callback
            )
            # 后台构建索引
            threading.Thread(target=self.retriever.build_index, daemon=True).start()
            # 启动自动更新（每5分钟）
            self.retriever.start_auto_update(interval=300)
            self.process_callback("[语义检索] 已初始化")

            
            # ✅ 就加这 4 行：后台预热模型
            threading.Thread(
                target=lambda: self.retriever.engine.encode(""),
                daemon=True
            ).start()
            self.process_callback("[语义检索] 后台预热中...")
        except Exception as e:
            self.process_callback(f"[语义检索] 初始化失败: {e}")
            self.retriever = None
        

            
        print("智能体核心加载完成")

    def _handle_plan_block(self, text: str) -> str:
        """处理 [PLAN][/PLAN] 块：提取并存储，返回清理后的文本"""
        pattern = r'\[PLAN\](.*?)\[/PLAN\]'
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        
        if match:
            plan_content = match.group(1).strip()
            # 保存到实例变量
            self.current_plan_block_text = plan_content
            # 保存到文件持久化
            try:
                with open("current_plan.txt", "w", encoding='utf-8') as f:
                    f.write(plan_content)
            except:
                pass
            self.process_callback(f"[计划块] 已更新计划 ({len(plan_content)} 字符)")
            # 从原文本中移除计划块
            return re.sub(pattern, '', text, flags=re.DOTALL | re.IGNORECASE).strip()
        return text

    def _process_inline_python(self, text: str) -> str:
        """处理内联 Python 代码，支持多个代码块，结果返回AI继续处理"""
        pattern = r'\[PYTHON\](.*?)\[/PYTHON\]'
        matches = list(re.finditer(pattern, text, re.DOTALL | re.IGNORECASE))
            
        if not matches:
            return text
        text = text.replace('[CONTINUE]', '').strip()

            
        # ===== 第一步：构建主屏显示文本（代码块替换为占位符），立即输出 =====
        display_parts = []
        last_end = 0
            
        for match in matches:
            before = text[last_end:match.start()].strip()
            if before:
                display_parts.append(before)
            display_parts.append("[执行Python代码...]")
            last_end = match.end()
            
        # 添加最后的普通文本
        after = text[last_end:].strip()
        if after:
            display_parts.append(after)
            
        final_display = "\n\n".join(display_parts)
        
        # 立即输出到主屏
        self.response_callback(final_display + "\n")
        self.db.add_message("assistant", final_display)
        if self._collab_enabled and self.ai.is_assistant_available():
            threading.Thread(target=self._generate_memory_summary, args=(final_display,), daemon=True).start()
            
        # ===== 第二步：执行所有代码块，副屏输出详细过程 =====
        results = []
        has_error = False
        error_msg = ""
            
        for i, match in enumerate(matches):
            code = match.group(1).strip()
                
            # 副屏输出：当前执行的代码块
            self.process_callback(f"\n[内联代码] 执行第 {i+1}/{len(matches)} 段:")
            self.process_callback(f"```python\n{code}\n```")
                
            stdout, stderr, success = self.sandbox.execute(code)
                
            if success:
                result = stdout if stdout else "执行成功（无输出）"
                # 副屏输出：执行结果
                self.process_callback(f"[内联代码] 第 {i+1} 段执行成功")
                if stdout:
                    self.process_callback(f"输出:\n{stdout}")
                results.append(result)
            else:
                error = stderr if stderr else "执行失败"
                # 副屏输出：错误信息
                self.process_callback(f"[内联代码] 第 {i+1} 段执行失败")
                self.process_callback(f"错误:\n{error}")
                has_error = True
                error_msg = error
                break
            
        # ===== 第三步：构建返回给AI的上下文 =====
        if has_error:
            ai_context = f"""刚才执行的 Python 代码失败了。
正在执行的代码：
{code}

错误信息：
{error_msg}

已执行到第 {len(results) + 1} 段代码（共 {len(matches)} 段），后续代码未执行。

请根据错误信息修正代码，或采取其他方式完成任务。
你的原始输出：
{text}"""
            self.act_log.append(f"[{datetime.now().strftime('%H:%M:%S')}]内联代码执行失败: {error_msg}")
        else:
            if results:
                summary = "\n".join([f"- {r}" for r in results])
                ai_context = f"""刚才执行了 {len(results)} 段 Python 代码，全部成功。

执行结果摘要：
{summary}

请评估执行结果，判断是需要回复用户，还是需要继续采取下一步行动。"""
            else:
                ai_context = "代码执行成功（无输出），请回复用户或者继续采取下一步行动。"
            self.act_log.append(f"[{datetime.now().strftime('%H:%M:%S')}]内联代码执行成功")
            
        # ===== 第四步：把执行结果返回AI =====
        self.db.add_message("system", ai_context)
            
        # 让AI继续处理
        return self._call_ai_and_process(ai_context)


    def _process_comodle_block(self, text: str) -> str:
        """处理 [COMODLE][/COMODLE] 块 - 调用辅助模型协作"""
        text = text.replace('[CONTINUE]', '').strip()
        pattern = r'\[COMODLE\](.*?)\[/COMODLE\]'
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        
        if not match:
            return text
        
        task_content = match.group(1).strip()
        if not task_content:
            return text
        
        self.process_callback(f"[协作] 收到辅助模型任务:\n{task_content}")
        
        result = self._use_assistant(task_content, {"source": "comodle_block"})
        
        # 保留原块，不变
        if result:
            self.process_callback(f"[协作] 辅助模型返回:\n{result}")
            return f"{text}\n\n[COMODLE_RESULT]\n{result}\n[/COMODLE_RESULT]\n\n[CONTINUE]"
        else:
            self.process_callback("[协作] 辅助模型无响应")
            return f"{text}\n\n[COMODLE_RESULT]\n协作模型无响应\n[/COMODLE_RESULT]\n\n[CONTINUE]"
        
    def _on_code_task_result(self, message: str):
        """处理代码任务结果回调"""
        try:
            # 将结果作为系统消息添加到对话历史
            self.db.add_message("system", message)
            self.process_callback(f"[代码任务] 执行结果已注入对话历史")

            prompt = f"{message}\n\n请根据上述结果自主行动。"
            
            # 复用现有的 _call_ai_and_process
            threading.Thread(target=self._call_ai_and_process, args=(prompt,), daemon=True).start()
            
        except Exception as e:
            self.process_callback(f"[代码任务] 处理失败: {e}")
        
    def _save_short_term_memory(self):
        """保存短期记忆到文件"""
        try:
            with open(self.MEMORY_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.short_term_memory, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.process_callback(f"[记忆] 保存失败: {e}")

    def _load_short_term_memory(self):
        try:
            if self.MEMORY_FILE.exists():
                with open(self.MEMORY_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        # 兼容旧格式：字符串转 dict
                        converted = []
                        for item in data:
                            if isinstance(item, dict):
                                converted.append(item)
                            else:
                                converted.append({"text": item, "weight": 3})
                        self.short_term_memory = converted
                        self.process_callback(f"[记忆] 加载了 {len(self.short_term_memory)} 条短期记忆")
        except Exception as e:
            self.process_callback(f"[记忆] 加载失败: {e}")
        
    def set_collaboration_enabled(self, enabled: bool):
        """动态切换协作模式"""
        self._collab_enabled = enabled
        # 同步更新全局变量（供其他模块使用）
        global ENABLE_ASSISTANT
        ENABLE_ASSISTANT = enabled
        
        if enabled:
            self.process_callback("[协作模式] 已启用")
            # 检查辅助模型可用性
            if self.ai.is_assistant_available():
                self.process_callback("[协作模式] 辅助模型可用")
            else:
                self.process_callback("[协作模式] 警告：辅助模型不可用，请检查本地服务")
        else:
            self.process_callback("[协作模式] 已禁用")
    
    def is_collaboration_enabled(self) -> bool:
        """获取协作模式状态"""
        return self._collab_enabled

    def add_memory(self, text: str, max_count: int = 20, weight: int = 5):
        # 存储为字典
        entry = {
            "text": text,
            "weight": weight,
            "timestamp": datetime.now().isoformat()
        }
        self.short_term_memory.append(entry)
        
        if len(self.short_term_memory) > max_count:
            # 取前 10 条，找权重最低的删除
            oldest = self.short_term_memory[:10]
            min_weight = 11
            min_idx = 0
            for i, item in enumerate(oldest):
                w = item.get("weight", 5)
                if w < min_weight:
                    min_weight = w
                    min_idx = i
            del self.short_term_memory[min_idx]
        
        self._save_short_term_memory()

    def get_memory_summary(self) -> str:
        if not self.short_term_memory:
            return ""
        lines = ["## 短时记忆（最近完成的事）"]
        for m in self.short_term_memory:
            if isinstance(m, dict):
                lines.append(f"- {m.get('text', '')}")
            else:
                lines.append(f"- {m}")
        return "\n".join(lines)

    def _generate_memory_summary(self, text: str = None, max_len: int = 100) -> Optional[str]:
        """使用辅助模型提炼文本摘要（通用方法）
        """
        if not text:
            return None        
        
        # 优先使用辅助模型
        prompt = f"""请将以下对话压缩为不超过{max_len}字的文字，供 AI 后续对话时参考。

## 压缩目标
- 让 AI 在未来能够了解之前发生了什么
- 必须忠实于压缩内容，不得编造，不得改变原意。
- 保留对话的基本要素和关键细节
- **必须包含时间信息**：在摘要开头标注时间，格式为 [HH:MM]
- 可以提供简短意见

### 压缩规则
1. 保留：用户的需求/问题、AI 的回答/结论、关键数据
2. 保留：**决策过程**（为什么这样做、为什么放弃其他方案、权衡依据）
3. 保留：**执行结果和遇到的问题**（成功/失败、错误信息）
4. 保留：**经验教训**（踩过的坑、得出的结论）
5. 忽略：重复内容、修饰语、纯粹的闲聊

## 输出格式
严格返回 JSON，不要其他内容：
{{
    "summary": "压缩后的摘要内容",
    "weight": 数字
}}

weight 取值 1-10，按重要性评分：
- 10：极其重要（用户明确指令、关键决策、核心数据）
- 7-9：重要（任务结果、工具使用方法、重要结论）  
- 4-6：一般（普通对话、一般信息）
- 1-3：低价值（闲聊、重复内容、临时信息）

## 待压缩内容
{text}"""
        result = self._use_assistant(prompt, {"task": "memory_summary"})
        if result and len(result) < 150:
            summary = result.strip()
            weight = 5
            obj = JSONParser.extract_json(result)
            if obj:
                summary = obj.get("summary", summary)
                weight = obj.get("weight", 5)
            self.add_memory(summary, weight=weight)
            self.process_callback(f"[短时记忆] {summary} (权重:{weight})")
            return summary
        
        # 回退：直接截取
        summary = text[:max_len] + ("..." if len(text) > max_len else "")
        self.add_memory(f"{summary}")
        self.process_callback(f"[短时记忆-回退] {summary}")
        return summary
    
    
    def _use_assistant(self, task: str, context: Dict = None) -> Optional[str]:
        """使用辅助模型处理子任务"""
        # 使用动态状态，而非全局常量
        if not self._collab_enabled:
            self.process_callback("[协作模式] 辅助模型未启用")
            return None
        # 不要传递code内容给辅助模型
        clean_context = context.copy() if context else {}
        if 'code' in clean_context:
            del clean_context['code']  # 移除代码上下文
        
        self.process_callback(f"[协作模式] 请求辅助模型处理")
        result = self.ai.request_assistant_help(task, clean_context, MAX_ASSISTANT_RETRIES)
        
        if result:
            self.process_callback(f"[协作模式] 辅助模型返回结果")
        else:
            self.process_callback("[协作模式] 辅助模型无响应")
        
        return result
    
    
    def _cleanup_plan(self):
        self.sandbox.force_cleanup()
    
    def update_activity(self):
        self.last_activity = time.time()
        self.last_introspection = time.time()
    
    def is_idle(self) -> bool:
        idle_time = time.time() - self.last_activity
        return not self.is_processing and idle_time > IDLE_THRESHOLD
    
    def do_introspection(self, force: bool = False) -> Dict:

        current_time = time.time()
        current_hour = datetime.now().hour
        peak_hours = [(9, 18)]  # 9:00-18:00 高峰
        is_peak = any(start <= current_hour < end for start, end in peak_hours)
        interval = 600 if is_peak else 300

        if not force and current_time - self.last_introspection < interval:
            reason = f"距离上次心跳仅 {current_time - self.last_introspection:.0f} 秒，需间隔 {interval} 秒"
            self.process_callback(f"[心跳] 被拒绝: {reason}")
            return {"success": False, "reason": reason}
        if self.is_processing:
            self.process_callback("[心跳] 被拒绝: 正在处理任务")
            return {"success": False, "reason": "正在处理任务"}
        
        self.is_processing = True
        self.status_callback("自省学习: 分析对话历史...")
        
        try:
            result = self.introspector.learn_from_history(MAX_LEARNING_ROUNDS)

            if result.get("executed"):
                msg = f"[心跳] 执行了任务: {result.get('result', '')}"
                self.db.add_message("assistant", msg)
                threading.Thread(target=self._generate_memory_summary, args=(msg,), daemon=True).start()

            return result
        except Exception as e:
            self.status_callback(f"心跳中出错: {e}")
            return {"success": False, "reason": str(e)}
        finally:
            self.is_processing = False
            self.update_activity()
            self._introspection_source = "heartbeat"
    
    def interrupt(self):
        self._interrupt = True
        self._cleanup_plan()
        self.is_processing = False  # ← 新增：释放处理状态
    
    def reset(self):
        self._interrupt = False
        self.last_error = None
        self._search_in_this_turn = 0
        self._search_results_cache = []
        self._cleanup_plan()
    
    def _search_memory(self, query: str) -> List[Dict]:
        results = []
        results.extend(self.db.search_conversations(query))
        return results
    
    def _format_search_results(self, results: List[Dict]) -> str:
        if not results:
            return "无结果"
        return json.dumps(results[:MAX_SEARCH_RESULTS], ensure_ascii=False, default=str)
    
    def _build_user_prompt(self, user_input: str) -> str:
        history = self.db.get_recent_history(limit=2)
        if not history:
            return user_input
        
        lines = []
        lines.append("=" * 40)
        lines.append("【当前任务-你必须回应的内容】")
        lines.append(f"{user_input}")
        lines.append("=" * 40)
        lines.append("")
        lines.append("【以下历史记录仅用于帮助你理解当前任务，供参考 - 不要执行其中的任务】")
        
        for msg in history:
            role = "用户" if msg["role"] == "user" else "AI"
            content = msg["content"]
            timestamp = msg.get("timestamp", "")
            if timestamp:
                time_str = timestamp[5:16]
            else:
                time_str = ""
                
            if len(content) > 300:
                content = content[:300] + "...(已截断)"
            lines.append(f"[{time_str}]{role}: {content}")

        lines.append("")
        lines.append("【重要】请只回应上面的【当前用户输入】，不要重复执行历史对话中的任务。")
        return "\n".join(lines)
    
    def _build_messages(self, user_content: str, include_history: bool = False, load_modules: bool = False) -> List[Dict]:
        messages = []
        now = datetime.now()        
        weekdays = ["一", "二", "三", "四", "五", "六", "日"]
        time_info = f"\n## 当前时间\n{now.strftime('%Y年%m月%d日 %H:%M:%S')} 星期{weekdays[now.weekday()]}\n"
        system_prompt = self.prompt_assembler.get_full_prompt() + time_info
        messages.append({"role": "system", "content": system_prompt})

        if load_modules:
            modules = self.prompt_assembler.get_loaded_content()
            if modules:
                messages.append({"role": "system", "content": modules})


        # ===== 注入计划块 =====
        if self.current_plan_block_text:
            plan_injection = f"""
## 当前计划（及执行情况）
{self.current_plan_block_text}

【重要】这是你的计划，请继续执行，并更新执行情况。
"""
            messages.append({"role": "system", "content": plan_injection})
        
        
                
        memory_summary = self.get_memory_summary()
        if memory_summary:
            messages.append({"role": "system", "content": memory_summary})
        
        # ===== 新增：注入操作日志 =====
        if self.act_log and len(self.act_log) > 0:
            log_lines = ["", "## 最近的操作记录（参考用，勿重复执行）:", ""]
            for entry in self.act_log:
                log_lines.append(f"- {entry}")
            messages.append({"role": "system", "content": "\n".join(log_lines)})
            
        # ===== 注入 MEMORY 变量 =====
        if hasattr(self, 'kb_interface'):
            var_content = self.kb_interface.manager.memory.get_injection_content()
            if var_content:
                messages.append({"role": "system", "content": var_content})
        
        # ===== 注入扩展列表（自动） =====
        if hasattr(self, 'ext_manager') and self.ext_manager:
            try:
                exts = self.ext_manager.db.get_active_extensions(limit=10)
                if exts:
                    names = [ext['name'] for ext in exts]
                    lines = [
                        "\n## 📦 可用扩展",
                        f"{', '.join(names)}",
                        "",
                        "**使用流程**:",
                        "1. 先 LOAD_MODULE 加载 extension 模块（拿说明书）",
                        "2. 再 get 查看具体参数",
                        "3. 最后 call 执行"
                    ]
                    messages.append({"role": "system", "content": "\n".join(lines)})
            except Exception:
                pass

        
        # ===== 语义检索注入 =====
        if hasattr(self, 'retriever') and self.retriever and not self._retrieval_done:
            try:
                config = get_config()
                general = config.get("general", {})
                top_k = general.get("embedding_top_k", 3)
                min_similarity = general.get("embedding_min_similarity", 0.25)
                
                results = self.retriever.retrieve_combined(
                    user_content, 
                    top_k=top_k,
                    min_similarity=min_similarity
                )
                
                if results and results.get('total', 0) > 0:
                    formatted = self.retriever.format_for_prompt(results)
                    if formatted:
                        messages.append({"role": "system", "content": formatted})
                        self.process_callback(f"[语义检索] 注入了 {results['total']} 条相关记录")
                        self._retrieval_done = True  
            except Exception as e:
                self.process_callback(f"[语义检索] 检索失败: {e}")
        
        if include_history:
            final_content = self._build_user_prompt(user_content)
        else:
            final_content = user_content

        if self._heartbeat_mode:
            messages.append({"role": "system", "content": Introspector.INTROSPECTION_SYSTEM})
        
        messages.append({"role": "user", "content": final_content})
        
        total_tokens = sum(TokenCounter.estimate(m["content"]) for m in messages)
        if total_tokens > MAX_CONTEXT_TOKENS:
            user_msg = messages[-1]
            truncated_content = TokenCounter.truncate(user_msg["content"], MAX_CONTEXT_TOKENS - 500)
            messages[-1] = {"role": "user", "content": truncated_content}
            self.status_callback("上下文超限，已截断")
        
        return messages
    
    def process(self, user_input: str) -> str:
        
        self._current_user_instruction = user_input
        self._retrieval_done = False
        self._search_in_this_turn = 0
        self._search_results_cache = []

        self._interrupt = False
        self.is_processing = True
        self.update_activity()
        
        # ===== 新增：标记为用户模式 =====
        self._is_user_loop = True
        self._heartbeat_mode = False
        
        self.status_callback("AI思考中...")
        self.act_log.append(f"[{datetime.now().strftime('%H:%M:%S')}]用户：{user_input}")
        try:
            self.db.add_message("user", user_input)
            messages = self._build_messages(user_input, include_history=True , load_modules=True)
            response = self.ai.chat(messages) 
            self.update_activity()
            if not response:
                self.status_callback("连接失败")
                return "AI连接失败"
            truncated = response[:1000] + ("..." if len(response) > 1000 else "")
            self.process_callback(f"AI 原始响应:\n{truncated}\n")
            
            result = self._process_response(response)
            self.status_callback("就绪")
            return result
        
        except Exception as e:
            self.status_callback(f"错误: {e}")
            self.act_log.append(f"[{datetime.now().strftime('%H:%M:%S')}]PLAN 指令格式不规范，导致脚本无法执行。错误信息：{e}")
            result = self._call_ai_and_process(f"你的 PLAN 指令格式不规范，导致脚本无法执行。错误信息：{e}\n\n") 
            return result
        finally:
            self.is_processing = False
            self.update_activity()
            self._is_user_loop = False

    
    def _process_response(self, response: str, retry_count: int = 0) -> str:
        """处理AI响应，支持多种格式"""
        response = self._process_memory_block(response)
        response = self._handle_plan_block(response)
        response = self._process_comodle_block(response)
        
        if '[PYTHON]' in response and '[/PYTHON]' in response:
            self.act_log.append(f"[{datetime.now().strftime('%H:%M:%S')}]AI使用了内联PYTHON代码")
            return self._process_inline_python(response)
        
        obj = JSONParser.extract_json(response)
        self.update_activity()
        if obj and 'action' in obj:
            response = response.replace('[CONTINUE]', '').strip()
            action = obj.get('action', '').upper()
            payload = obj.get('payload', {})
            self.process_callback(f"识别指令: {action}\n")
            self.process_callback(f"AI返回内容: {response}\n")
            result = self._execute_instruction(action, payload, obj)
            return result if result else "执行完成"
        else:
            trimmed = response.strip()
            if trimmed and trimmed[0] in ('{', '['):
                error_msg = f"JSON解析失败，请检查格式（特别是三引号）。你刚才的指令：{response}"
                return self._call_ai_and_process(error_msg)
    
            self.process_callback("无指令块，作为普通回复\n")
            self.act_log.append(f"[{datetime.now().strftime('%H:%M:%S')}]脚本未发现指令块，将AI输出作为普通回复处理")

            # ===== 检测 [继续] 标记 =====
            if '[CONTINUE]' in response:
                self.process_callback("检测到 [继续] 标记，让AI继续...")
                current_heartbeat_mode = self._heartbeat_mode
                try:
                    # ===== 直接调用AI，不经过 _call_ai_and_process =====
                    prompt = f"你刚才的回复中有 [继续] 标记，脚本现在给予你再一次行动的能力。"
                    messages = self._build_messages(prompt, include_history=True, load_modules=True)
                    next_response = self.ai.chat(messages)
                    
                    if next_response:
                        self._heartbeat_mode = current_heartbeat_mode
                        return self._process_response(next_response)
                    else:
                        return "AI无响应"
                finally:
                    self._heartbeat_mode = current_heartbeat_mode
            self.db.add_message("assistant", response)
            if self._collab_enabled and self.ai.is_assistant_available():
                user_input = getattr(self, '_current_user_instruction', '')
                time_str = datetime.now().strftime("%H:%M")
                text = f"[{time_str}] 用户: {user_input}\nAI: {response}"
                threading.Thread(target=self._generate_memory_summary, args=(text,), daemon=True).start()
            
            return response
        
    def _process_memory_block(self, text: str) -> str:
        """处理 [MEMORY][/MEMORY] 块 - 快速添加单条记忆"""
        pattern = r'\[MEMORY\](.*?)\[/MEMORY\]'
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        
        if not match:
            return text
        
        memory_content = match.group(1).strip()
        if not memory_content:
            return text
        
        self.process_callback(f"[记忆] 收到 MEMORY 块: {memory_content}")
        
        # 检查是否已存在相同记忆（去重）
        existing = self.kb_interface.manager.memory.list_all()
        is_duplicate = False
        for item in existing:
            if item.get('value') == memory_content:
                is_duplicate = True
                break
        
        if is_duplicate:
            msg = f"⏭️ 记忆已存在，跳过: {memory_content[:30]}..."
            self.process_callback(f"[记忆] {msg}")
            self.response_callback(f"[系统] {msg}\n")
        else:
            # 添加记忆
            result = self.kb_interface.execute_command({
                'action': 'VAR_ADD',
                'payload': {'value': memory_content}
            })
            if '成功' in str(result):
                msg = f"已添加记忆: {memory_content[:50]}"
                self.response_callback(f"[系统] {msg}\n")
                self.db.add_message("system", msg)
                self.act_log.append(f"[{datetime.now().strftime('%H:%M:%S')}]AI添加记忆: {memory_content[:30]}...")
            else:
                msg = f"记忆添加失败: {result}"
                self.process_callback(f"[记忆] {msg}")
                self.response_callback(f"[系统] {msg}\n")
        
        # 从原文本中移除 MEMORY 块
        cleaned = re.sub(pattern, '', text, flags=re.DOTALL | re.IGNORECASE).strip()
        return cleaned
        
    
    def _execute_instruction(self, action: str, payload: Any, instruction_block: Dict) -> str:
        """执行指令块"""
        
        self._search_injected = False
        if action == "SEARCH":
            return self._execute_search(payload)
        elif action == "CREATE_TASK" or action == "CREATE_CODE_TASK":
            task = create_task_from_ai({"action": "CREATE_TASK", "payload": payload})
            if task:
                msg = f"任务已创建: {task.id} - {task.name}"
                self.process_callback(f"[任务] {msg}")
                return self._continue_original_task(msg)
            return "任务创建失败"
        elif action == "UPDATE_TASK":  
            return self._execute_update_task(payload)
        elif action == "EXTENSION":
            if not self.ext_manager:
                return "扩展系统未初始化"
            
            # 如果没有指定 operation，默认是 call
            if "operation" not in payload:
                payload["operation"] = "call"
            
            result = self.ext_manager.execute_manage(payload)
            self.db.add_message("assistant", f"[扩展] {result[:200]}")
            return self._continue_original_task(f"已执行 EXTENSION，结果：{result}")
        elif action == "LOAD_MODULE":
            return self._execute_load_module(payload)
        
        elif action == "KNOWLEDGE":
            return self._execute_knowledge(payload)
        
        elif action == "MEMORY":
            return self._execute_variable(payload)
        else:
            original = json.dumps(instruction_block, ensure_ascii=False, indent=2) if instruction_block else "无"
            self.process_callback(f" 未知指令: {action}\n")
            return self._call_ai_and_process(
                f"你返回的指令 '{action}' 脚本不认识。\n"
                f"你刚才返回的指令是：\n{original}\n\n"
                f"请严格按提示词要求规范使用指令块。"
            )

    def _continue_original_task(self, prompt: str) -> str:
        """执行完一个操作后，继续执行原始任务"""
        original_task = getattr(self, '_current_user_instruction', '')
        if original_task:
            return self._call_ai_and_process(
                f"{prompt}\n\n"
                f"原始任务：{original_task}\n"
                f"请根据刚才的结果，继续执行下一步。"
            )
        return prompt

    def _execute_knowledge(self, payload: Dict) -> str:
        """执行知识库操作"""
        if not hasattr(self, 'kb_interface'):
            return "知识库系统未初始化"
        
        operation = payload.get('operation', '')
        action = f"KNOWLEDGE_{operation.upper()}"
        
        result = self.kb_interface.execute_command({
            'action': action,
            'payload': payload
        })
        
        self.act_log.append(f"[{datetime.now().strftime('%H:%M:%S')}]AI进行了知识库操作{action}")
        return self._continue_original_task(f"已执行 {operation}，结果：{result}")

    def _execute_variable(self, payload: Dict) -> str:
        """执行提示词变量操作"""
        if not hasattr(self, 'kb_interface'):
            return "知识库系统未初始化"
        
        operation = payload.get('operation', '')
        action = f"VAR_{operation.upper()}"
        
        result = self.kb_interface.execute_command({
            'action': action,
            'payload': payload
        })
        return self._continue_original_task(f"已执行 {operation}，结果：{result}，请不要再次进行操作，以免陷入循环被系统中断")
    
    def _execute_load_module(self, payload: Dict) -> str:
        modules = payload.get("modules", [])
        reason = payload.get("reason", "AI需要该功能")
        result = self.prompt_assembler.load_modules(modules, reason)
        
        # 输出到主屏和副屏
        msg = result.get("message", "模块加载完成")
        self.response_callback(f"[系统][LOAD_MODULE] {msg}")
        self._call_depth = 0
        self.process_callback(f"[系统] 循环计数器已重置为 0")
        self.act_log.append(f"[{datetime.now().strftime('%H:%M:%S')}]AI加载了模块{modules}")
        if result.get('success'):
            return self._continue_original_task(msg)

    def _execute_update_task(self, payload: Dict) -> str:
        task_id = payload.get("task_id")
        if not task_id:
            return "缺少 task_id"
        
        # 提取所有可更新字段
        update_kwargs = {}
        allowed_fields = [
            'name', 'description', 'status', 'priority', 'progress',
            'result', 'error', 'scheduled_at', 'repeat_interval',
            'max_repeats', 'tags', 'dependencies', 'metadata',
            'code_to_execute', 'max_auto_execute', 'execute_on_schedule'
        ]
        for key in allowed_fields:
            if key in payload:
                update_kwargs[key] = payload[key]
        
        if not update_kwargs:
            return "无有效更新字段"
        
        task = self.mission_manager.update_task(task_id, **update_kwargs)
        
        if task:
            msg = f"任务 {task_id} 已更新"
            self.act_log.append(f"[{datetime.now().strftime('%H:%M:%S')}]任务{task_id}: 【{task.name}】 已更新")
        else:
            msg = f"任务 {task_id} 更新失败（可能已被归档或不存在）"
            self.act_log.append(f"[{datetime.now().strftime('%H:%M:%S')}]任务{task_id}: 更新失败")
        
        return self._continue_original_task(msg)
    
    def _execute_search(self, payload: Dict) -> str:
        """执行SEARCH指令"""
        self._search_injected = True 
        self._search_in_this_turn += 1
        user_instruction = payload.get("user_or_instruction", "")
        if not user_instruction:
            user_instruction = getattr(self, '_current_user_instruction', '')

        query = payload.get("query", "")
        
        if self._search_in_this_turn >= 3:
            self.process_callback("连续搜索超过3次，禁止继续搜索\n")
            search_history = ""
            for i, s in enumerate(self._search_results_cache, 1):
                search_history += f"\n=== 搜索 {i}: {s['query']} (找到 {s['count']} 条) ===\n"
                search_history += s['results'] + "\n"
            
            prompt = f"搜索次数已达上限（3次）。\n请根据历史搜索结果直接回答用户问题（{user_instruction}）。\n历史搜索结果：\n\n{search_history}。\n"
            return self._call_ai_and_process(prompt)
        
        results = self._search_memory(query)
        formatted = self._format_search_results(results)
        search_record = f"AI进行了与 {query}有关的回忆，找到 {len(results)} 条相关记忆(长度{len(formatted)}字符)"
        self.process_callback(search_record,"search")
        
        self._search_results_cache.append({
            'query': query,
            'count': len(results),
            'results': formatted
        })

        if len(self._search_results_cache) > 3:
            self._search_results_cache = self._search_results_cache[-3:]       
      

        prompt = f"""你执行了 SEARCH 指令，搜索关键词为："{query}"

原始任务/指令：{user_instruction}
这是第{self._search_in_this_turn}次搜索
搜索结果：
{formatted}

请根据这些搜索结果，继续完成你的原始任务。如果搜索结果不足以完成任务，请说明并采取其他行动。
注意：连续搜索会限制次数，防止陷入沉思。"""
        
        self.act_log.append(f"[{datetime.now().strftime('%H:%M:%S')}]AI进行了第{self._search_in_this_turn}次与 【{query}】有关的回忆")
        return self._call_ai_and_process(prompt)
    
    def _call_ai_and_process(self, prompt: str) -> str:

        if self._interrupt:        
             return "已中断循环"

            
        """调用AI并处理响应（使用主模型）"""
        if not hasattr(self, '_call_depth'):
            self._call_depth = 0
        if "失败" in prompt or "错误" in prompt:
            self._search_injected = False
        
        self._call_depth += 1
        try:
            if self._heartbeat_mode and self._call_depth > 3:
                error = "检测到循环（超过3次重试），已自动停止。"
                self.status_callback(error)
                self._cleanup_plan()
                self.db.add_message("system", error)
                self.is_processing = False

                return error
            if self._search_results_cache and not self._search_injected:
                latest = self._search_results_cache[-1]
                results_text = latest.get('results', '')
                if results_text and results_text != '无结果':
                    prompt = f"【上一次搜索结果】\n{results_text}\n\n{prompt}"
                    self._search_injected = True
            
            # ===== 修改：显示循环次数信息 =====
            if self._heartbeat_mode:
                prompt = f"\n【循环次数】\n目前是第{self._call_depth}次，第3次将中断执行，返回用户。\n\n{prompt}"
            else:
                prompt = f"\n【循环次数】\n目前是第{self._call_depth}次循环\n\n{prompt}"
                
            messages = self._build_messages(prompt, include_history=True , load_modules=True)
            self.act_log.append(f"[{datetime.now().strftime('%H:%M:%S')}]AI进行第{self._call_depth}次循环操作")
            response = self.ai.chat(messages)  # 使用主模型
            if response:
                self.update_activity()
                self.process_callback(f"[接收] {len(response)} 字符")
                result = self._process_response(response)
                return result if result else "执行完成"
            return "AI无响应"
        finally:
            if self._call_depth > 0:
                self._call_depth -= 1



# ============================================================================
# 心跳线程
# ============================================================================

class Heartbeat:
    def __init__(self, agent: AIAgent, status_callback=None):
        self.agent_ref = weakref.ref(agent)
        self.status_callback = status_callback or (lambda x: None)
        self.running = False
        self.thread = None
        self.last_introspection = time.time()
        print("心跳机制初始化完成")
    
    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self.status_callback("心跳服务已启动")
    
    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=3)
    
    def _run(self):
        while self.running:

            time.sleep(60)
            heartbeat_path = os.path.abspath("heartbeat.txt") 
            try:
                with open(heartbeat_path, "w") as f:
                    f.write(str(time.time()))
            except:
                pass
            
            agent = self.agent_ref()
            if agent is None:
                print("Agent已销毁，停止心跳")
                break
            
            if not agent.is_idle():
                continue
            
            result = agent.do_introspection()            
            self.last_introspection = time.time()


# ============================================================================
# UI 界面 - 双屏设计
# ============================================================================

class AgentUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("AI Agent v9.9 - 双模型协作")
        self.root.geometry("1200x800")
        self.root.configure(bg='#1e1e1e')
        
        # 1. 先创建所有控件
        self._create_widgets()
        
        # 2. 然后创建 Agent（会触发回调）
        self.agent = AIAgent(
            status_callback=self.update_status,
            response_callback=self.append_response,
            process_callback=self.append_process
        )
        
        self.heartbeat = Heartbeat(self.agent, self.update_status)
        
        self.root.bind('<Escape>', self.on_interrupt)
        self.root.bind('<Control-Return>', self.on_send)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        
        self.root.after(100, self._show_startup)
        self.heartbeat.start()
        
        # 3. Agent 创建后更新 UI 状态
        self.root.after(200, self._update_collab_ui)
        print("UI界面启动完成")
    
    def _create_widgets(self):
        main_frame = tk.Frame(self.root, bg='#1e1e1e')
        main_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        paned = ttk.PanedWindow(main_frame, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)
        
        # 左侧主屏
        left_frame = tk.Frame(paned, bg='#1e1e1e')
        paned.add(left_frame, weight=1)
        
        tk.Label(left_frame, text="对话主屏", bg='#1e1e1e', fg='#4ec9b0',
                 font=("Consolas", 10, "bold")).pack(fill=tk.X, pady=(0, 5))
        
        self.display = scrolledtext.ScrolledText(
            left_frame, wrap=tk.WORD, font=("Consolas", 10),
            bg='#1e1e1e', fg='#d4d4d4', insertbackground='white'
        )
        self.display.pack(fill=tk.BOTH, expand=True)
        self.display.config(state=tk.DISABLED)
        
        self.display.tag_config("user", foreground="#4ec9b0")
        self.display.tag_config("ai", foreground="#569cd6")
        self.display.tag_config("system", foreground="#cca700")
        self.display.tag_config("error", foreground="#f44747")
        self.display.tag_config("timestamp", foreground="#6a9955")
        self.display.tag_config("info", foreground="#808080")
        self.display.tag_config("output", foreground="#9cdcfe")
        
        # 右侧副屏
        right_frame = tk.Frame(paned, bg='#1e1e1e')
        paned.add(right_frame, weight=1)
        
        tk.Label(right_frame, text="中间过程（回忆/计划/代码/工具/协作）", bg='#1e1e1e', 
                 fg='#ce9178', font=("Consolas", 10, "bold")).pack(fill=tk.X, pady=(0, 5))
        
        self.process_display = scrolledtext.ScrolledText(
            right_frame, wrap=tk.WORD, font=("Consolas", 9),
            bg='#0d0d0d', fg='#b0b0b0', insertbackground='white'
        )
        self.process_display.pack(fill=tk.BOTH, expand=True)
        self.process_display.config(state=tk.DISABLED)
        
        self.process_display.tag_config("search", foreground="#4ec9b0")
        self.process_display.tag_config("plan", foreground="#cca700")
        self.process_display.tag_config("code", foreground="#9cdcfe")
        self.process_display.tag_config("result", foreground="#6a9955")
        self.process_display.tag_config("error", foreground="#f44747")
        self.process_display.tag_config("info", foreground="#808080")
        self.process_display.tag_config("timestamp", foreground="#6a9955")
        self.process_display.tag_config("collaboration", foreground="#c586c0")
        
        # 底部状态栏
        status_frame = tk.Frame(self.root, bg='#2d2d2d')
        status_frame.pack(fill=tk.X, padx=5, pady=(0, 5))
        
        self.status_var = tk.StringVar(value="就绪")
        tk.Label(status_frame, textvariable=self.status_var,
                 bg='#2d2d2d', fg='#6a9955', anchor=tk.W,
                 font=("Consolas", 9)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5, pady=3)
        
        self.learning_indicator = tk.Label(status_frame, text="O", bg='#2d2d2d', 
                                           fg='gray', font=("Consolas", 10))
        self.learning_indicator.pack(side=tk.RIGHT, padx=5)
        
        # 协作状态
        self.collab_indicator = tk.Label(status_frame, text="", bg='#2d2d2d',
                                         fg='#c586c0', font=("Consolas", 10))
        self.collab_indicator.pack(side=tk.RIGHT, padx=5)
        
        # 输入区域
        input_frame = tk.Frame(self.root, bg='#1e1e1e')
        input_frame.pack(fill=tk.X, padx=5, pady=(0, 5))
        
        tk.Label(input_frame, text="输入 (Ctrl+Enter发送, Esc中断):",
                 bg='#1e1e1e', fg='#888888', anchor=tk.W).pack(fill=tk.X)
        
        self.input_text = tk.Text(input_frame, height=4, font=("Consolas", 10),
                                   bg='#2d2d2d', fg='#d4d4d4', insertbackground='white')
        self.input_text.pack(fill=tk.X)
        self.input_text.bind("<Return>", self.on_enter)
        self.input_text.bind("<Shift-Return>", lambda e: None)
        
        # 按钮栏
        btn_frame = tk.Frame(self.root, bg='#1e1e1e')
        btn_frame.pack(fill=tk.X, padx=5, pady=(0, 5))
        
        buttons = [
            ("发送 (Ctrl+Enter)", self.on_send, '#0e639c'),
            ("清空对话", self.on_clear, '#3c3c3c'),
            ("中断 (Esc)", self.on_interrupt, '#8a2b2b'),
            ("导出日志", self.on_export, '#3c3c3c'),
            ("查看状态", self.show_status, '#3c3c3c'),
            ("清空过程", self.clear_process, '#3c3c3c'),
            ("协作状态", self.show_collab_status, '#3c3c3c'),
            ("查看扩展", self.show_extensions, '#0e639c'),
            ("触发心跳", self.trigger_heartbeat, '#8a2b2b'),
            ("系统配置", self.open_config, '#8a2b2b'),
            ("重建语义索引", self.rebuild_embedding_index, '#8a2b2b'),
            ("退出程序", self.on_close, '#8a2b2b'),
        ]
        
        for text, cmd, color in buttons:
            tk.Button(btn_frame, text=text, command=cmd,
                      bg=color, fg='white', relief=tk.FLAT, padx=8).pack(side=tk.LEFT, padx=2)
    
        # 🆕 协作模式开关
        self.collab_var = tk.BooleanVar(value=ENABLE_ASSISTANT)
        self.collab_btn = tk.Checkbutton(
            btn_frame, 
            text=" 协作模式",
            variable=self.collab_var,
            command=self.toggle_collaboration,
            bg='#1e1e1e',
            fg='#c586c0',
            selectcolor='#2d2d2d',
            font=("Consolas", 9)
        )
        self.collab_btn.pack(side=tk.LEFT, padx=10)

     
    def rebuild_embedding_index(self):
        """重建语义索引"""
        if not hasattr(self.agent, 'retriever') or not self.agent.retriever:
            self.append_process("[语义检索] 未初始化", "error")
            return
        
        if messagebox.askyesno("确认", "重建语义索引将删除所有现有向量并重新生成，可能需要几分钟，确定吗？"):
            self.append_process("[语义检索] 开始重建索引...", "info")
            threading.Thread(target=self._do_rebuild_index, daemon=True).start()

    def _do_rebuild_index(self):
        try:
            self.agent.retriever.rebuild_all()
            self.root.after(0, lambda: self.append_process("[语义检索] 索引重建完成", "result"))
        except Exception as e:
            self.root.after(0, lambda: self.append_process(f"[语义检索] 重建失败: {e}", "error"))   

    def open_config(self):
        """打开配置窗口"""
        from config_manager import show_config_window
        
        def on_config_saved():
            try:
                from ai_client import reload_config, create_collaborative_client
                reload_config()
                
                if hasattr(self, 'agent') and self.agent:
                    # 重新创建 AI 客户端
                    self.agent.ai = create_collaborative_client(
                        stats_callback=self.agent.process_callback
                    )
                    self.append_process("[配置] 配置已更新并重新加载", "info")
                    self.update_status("配置已更新")
                    if hasattr(self, '_update_collab_ui'):
                        self._update_collab_ui()
            except Exception as e:
                self.append_process(f"[配置] 重新加载失败: {e}", "error")
        
        show_config_window(self.root, on_save=on_config_saved)
        
    def trigger_heartbeat(self):
        """手动触发心跳"""
        if not hasattr(self, 'agent') or self.agent.is_processing:
            return
        self.append_process("手动触发心跳...", "info")
        threading.Thread(target=lambda: self.agent.do_introspection(force=True), daemon=True).start()

    def show_extensions(self):
        """查看扩展列表（只读）"""
        if not hasattr(self.agent, 'ext_manager') or not self.agent.ext_manager:
            self._add_message("系统", "扩展系统未初始化", "error")
            return
        
        win = tk.Toplevel(self.root)
        win.title("扩展列表")
        win.geometry("800x500")
        win.configure(bg='#1e1e1e')
        
        # 工具栏
        toolbar = tk.Frame(win, bg='#2d2d2d')
        toolbar.pack(fill=tk.X, padx=5, pady=5)
        tk.Label(toolbar, text="扩展列表 (只读查看)", bg='#2d2d2d', fg='#4ec9b0',
                 font=("Consolas", 10, "bold")).pack(side=tk.LEFT, padx=5)
        
        # 主布局
        paned = ttk.PanedWindow(win, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # 左侧列表
        left_frame = tk.Frame(paned, bg='#1e1e1e')
        paned.add(left_frame, weight=1)
        listbox = tk.Listbox(left_frame, bg='#2d2d2d', fg='#d4d4d4',
                             font=("Consolas", 10), selectmode=tk.SINGLE)
        listbox.pack(fill=tk.BOTH, expand=True)
        
        # 右侧详情
        right_frame = tk.Frame(paned, bg='#1e1e1e')
        paned.add(right_frame, weight=1)
        tk.Label(right_frame, text="详细信息", bg='#1e1e1e', fg='#ce9178',
                 font=("Consolas", 10, "bold")).pack(fill=tk.X, pady=(0, 5))
        info_text = scrolledtext.ScrolledText(right_frame, wrap=tk.WORD,
                                              font=("Consolas", 10),
                                              bg='#0d0d0d', fg='#b0b0b0')
        info_text.pack(fill=tk.BOTH, expand=True)
        
        def refresh():
            """刷新列表"""
            listbox.delete(0, tk.END)
            info_text.config(state=tk.NORMAL)
            info_text.delete("1.0", tk.END)
            info_text.insert(tk.END, "选择左侧扩展查看详情")
            info_text.config(state=tk.DISABLED)
            
            try:
                exts = self.agent.ext_manager.db.get_extensions(limit=50)
                if not exts:
                    listbox.insert(tk.END, "暂无扩展")
                    return
                for ext in exts:
                    mark = "✓" if ext['status'] == 'active' else "✗"
                    listbox.insert(tk.END, f"[{ext['id']:3d}] {mark} {ext['name']:<20} v{ext['version']:<8}")
            except Exception as e:
                listbox.insert(tk.END, f"加载失败: {e}")
        
        def show_detail(event):
            """显示详情"""
            sel = listbox.curselection()
            if not sel:
                return
            import re
            match = re.search(r'\[(\d+)\]', listbox.get(sel[0]))
            if not match:
                return
            
            info = self.agent.ext_manager.get_extension_info(int(match.group(1)))
            info_text.config(state=tk.NORMAL)
            info_text.delete("1.0", tk.END)
            
            if 'error' in info:
                info_text.insert(tk.END, f"错误: {info['error']}")
            else:
                lines = [
                    f"名称: {info['name']}",
                    f"ID: {info['id']}",
                    f"描述: {info['description']}",
                    f"版本: {info['version']}",
                    f"状态: {info['status']}",
                    f"作者: {info.get('author', '未指定')}",
                    f"导入次数: {info['import_count']}",
                    f"最后使用: {info['last_used_at'] or '从未'}",
                    f"创建时间: {info['created_at']}",
                    f"超时时间: {info.get('timeout', 30)}秒",
                    f"依赖: {', '.join(info.get('dependencies', [])) or '无'}",
                ]
                if info.get('usage_guide'):
                    lines.append(f"\n使用说明:\n{info['usage_guide']}")
                if info.get('version_history'):
                    lines.append(f"\n版本历史:")
                    for v in info['version_history']:
                        lines.append(f"  v{v['version']} - {v['created_at'][:19]}")
                info_text.insert(tk.END, "\n".join(lines))
            
            info_text.config(state=tk.DISABLED)
        
        listbox.bind('<<ListboxSelect>>', show_detail)
        tk.Button(toolbar, text="刷新", command=refresh,
                  bg='#0e639c', fg='white').pack(side=tk.LEFT, padx=2)
        refresh()
          
    def _update_collab_ui(self):
        """更新协作模式UI状态"""
        try:
            is_enabled = self.collab_var.get()
            if is_enabled:
                self.collab_btn.config(fg='#4ec9b0', text=' 协作模式 (ON)')
                if hasattr(self, 'collab_indicator'):
                    #  检查 agent 是否存在且可用
                    if hasattr(self, 'agent') and self.agent.ai.is_assistant_available():
                        self.collab_indicator.config(fg='#c586c0', text='')
                    else:
                        self.collab_indicator.config(fg='gray', text='')
            else:
                self.collab_btn.config(fg='#888888', text=' 协作模式 (OFF)')
                if hasattr(self, 'collab_indicator'):
                    self.collab_indicator.config(fg='gray', text='  ')
        except Exception as e:
            # 忽略初始化时的错误
            pass
    
    def toggle_collaboration(self):
        """切换协作模式"""
        is_enabled = self.collab_var.get()
        self.agent.set_collaboration_enabled(is_enabled)

        
        # 更新UI
        self._update_collab_ui()
        self.update_status(f"协作模式 {'已启用' if is_enabled else '已禁用'}")
        
        if is_enabled:
            # 检查辅助模型是否可用
            if self.agent.ai.is_assistant_available():
                self.append_process("[协作模式] 辅助模型已启用并可用", "collaboration")
            else:
                self.append_process("[协作模式] 辅助模型已启用但不可用，请检查本地模型服务", "error")
        else:
            self.append_process("[协作模式] 已禁用辅助模型", "info")

            
    
    def _show_startup(self):
        self._add_message("系统", 
            "AI Agent v9.9 已启动 - 双模型协作版本\n\n"
            "核心特性:\n"
            "  双屏设计：左侧对话主屏，右侧中间过程\n"
            "  双模型协作：主模型负责指令交互，辅助模型处理子任务\n"
            "  指令可选：AI可以自由选择输出格式\n"
            "  多步骤任务：使用 [PLAN] + [PYTHON] + [CONTINUE] 组合\n"
            f"  辅助模型状态: {' 已就绪' if (ENABLE_ASSISTANT and self.agent.ai.is_assistant_available()) else ' 不可用'}\n\n", "system")

        self.agent.db.add_message("system", "系统启动")
        
        if self.agent.ai.test_connection():
            self.update_status("AI已连接 | 协作模式")
        else:
            self.update_status("AI连接失败")
            self._add_message("系统", "AI连接失败", "error")
    
    def _add_message(self, sender: str, msg: str, tag: str):
        """在主屏添加消息"""
        # ===== 过滤 Emoji（防止 TclError） =====
        import re
        # 移除所有 Emoji（U+1F000 及以上）
        msg = re.sub(r'[\U0001F000-\U0001FFFF]', '', msg)
        # 移除常见符号（U+2600-U+27BF）
        msg = re.sub(r'[\u2600-\u27BF]', '', msg)
        # 移除零宽字符
        msg = re.sub(r'[\u200B-\u200F\u2028-\u202F\uFEFF]', '', msg)
        # 移除其他可能导致问题的字符
        msg = re.sub(r'[\u2700-\u27BF]', '', msg)
        # ===== 过滤结束 =====
        
        def do_add():
            self.display.config(state=tk.NORMAL)
            MAX_LINES = 1000
            line_count = int(self.display.index('end-1c').split('.')[0])
            if line_count > MAX_LINES:
                self.display.delete('1.0', f'{MAX_LINES // 2}.0')
                self.display.insert(tk.END, "[旧消息已清理]\n\n", "system")
            
            time_str = datetime.now().strftime("%H:%M:%S")
            self.display.insert(tk.END, f"[{time_str}] ", "timestamp")
            local_tag = tag
            if local_tag is None and sender == "系统":
                if "执行代码" in msg or "AI生成" in msg or "步骤" in msg:
                    local_tag = "info"
                elif "执行结果" in msg or "输出:" in msg:
                    local_tag = "output"
                else:
                    local_tag = "system"
            else:
                local_tag = local_tag or "system"
        
            self.display.insert(tk.END, f"{sender}:\n", local_tag)
        
            if "```" in msg:
                parts = msg.split("```")
                for i, part in enumerate(parts):
                    if i % 2 == 1:
                        self.display.insert(tk.END, part, "ai")
                    else:
                        self.display.insert(tk.END, part, local_tag)
            else:
                self.display.insert(tk.END, msg, local_tag)
        
            self.display.insert(tk.END, "\n\n")
            self.display.see(tk.END)
            self.display.config(state=tk.DISABLED)
        try:
            self.root.after(0, do_add)
        except (tk.TclError, RuntimeError):
            pass
    
    def append_response(self, chunk: str, append: bool = False):
        """在主屏追加响应"""
        self._add_message("AI", chunk, "ai")
    
    def append_process(self, text: str, tag: str = None):
        """在副屏添加中间过程"""
    
        # ===== 过滤 Emoji =====
        import re
        text = re.sub(r'[\U0001F000-\U0001FFFF]', '', text)
        text = re.sub(r'[\u2600-\u27BF]', '', text)
        text = re.sub(r'[\u200B-\u200F\u2028-\u202F\uFEFF]', '', text)
        # ===== 过滤结束 =====
        def do_add():
            self.process_display.config(state=tk.NORMAL)
            MAX_LINES = 1000
            line_count = int(self.process_display.index('end-1c').split('.')[0])
            if line_count > MAX_LINES:
                self.process_display.delete('1.0', f'{MAX_LINES // 2}.0')
                self.process_display.insert(tk.END, "[旧过程已清理]\n", "info")
            
            time_str = datetime.now().strftime("%H:%M:%S")
            self.process_display.insert(tk.END, f"[{time_str}] ", "timestamp")
        
            local_tag = tag
            if local_tag is None:
                if "SEARCH" in text or "搜索" in text:
                    local_tag = "search"
                elif "PLAN" in text or "计划" in text:
                    local_tag = "plan"
                elif "执行代码" in text or "代码" in text or "```" in text:
                    local_tag = "code"
                elif "执行结果" in text or "找到" in text:
                    local_tag = "result"
                elif "失败" in text or "错误" in text:
                    local_tag = "error"
                elif "协作" in text or "辅助模型" in text:
                    local_tag = "collaboration"
                else:
                    local_tag = "info"
        
            self.process_display.insert(tk.END, text, local_tag)
            self.process_display.insert(tk.END, "\n")
            self.process_display.see(tk.END)
            self.process_display.config(state=tk.DISABLED)

        try:
            self.root.after(0, do_add)
        except (tk.TclError, RuntimeError):
            pass
    
    def update_status(self, msg: str):
        def update():
            try:
                self.status_var.set(msg)
                if "学习" in msg:
                    self.learning_indicator.config(text="*", fg='#4ec9b0')
                    self.root.after(3000, lambda: self.learning_indicator.config(text="O", fg='gray'))
            except (tk.TclError, RuntimeError):
                pass
        try:
            self.root.after(0, update)
        except (tk.TclError, RuntimeError):
            pass
    
    def on_enter(self, event):
        if not event.state & 0x1:
            self.on_send()
            return "break"
    
    def on_send(self, event=None):
        user_input = self.input_text.get("1.0", tk.END).strip()
        if not user_input:
            return
        
        if not self.agent.is_processing:
            self.agent.reset()
        
        self.input_text.delete("1.0", tk.END)
        self._add_message("用户", user_input, "user")
        
        def process():
            try:
                response = self.agent.process(user_input)
                if response :
                    self.root.after(0, lambda: self._add_message("AI", response, "ai"))
            except Exception as e:
                self.root.after(0, lambda: self._add_message("系统", f"错误: {e}", "error"))
        
        threading.Thread(target=process, daemon=True).start()
    
    def clear_process(self):
        """清空副屏"""
        self.process_display.config(state=tk.NORMAL)
        self.process_display.delete("1.0", tk.END)
        self.process_display.config(state=tk.DISABLED)
    
    def on_clear(self):
        if messagebox.askyesno("确认", "清空所有对话？"):
            self.agent.db.clear_conversations()
            self.agent.reset()
            self.display.config(state=tk.NORMAL)
            self.display.delete("1.0", tk.END)
            self.display.config(state=tk.DISABLED)
            self._add_message("系统", "对话已清空", "system")
    
    def on_interrupt(self, event=None):
        self.agent.interrupt()
        self.update_status("已中断")
        self._add_message("系统", "用户中断了当前操作", "system")
        self.append_process("用户中断了当前操作", "error")
    
    def on_export(self):
        try:
            filename = f"agent_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(f"AI Agent 日志 - {datetime.now()}\n{'='*60}\n\n")
                f.write("=== 对话主屏 ===\n")
                f.write(self.display.get("1.0", tk.END))
                f.write("\n\n=== 中间过程 ===\n")
                f.write(self.process_display.get("1.0", tk.END))
            self.update_status(f"已导出 {filename}")
        except Exception as e:
            self.update_status(f"导出失败: {e}")
    
    def show_status(self):
        self.root.update_idletasks()
        win = tk.Toplevel(self.root)
        win.title("系统状态")
        win.geometry("600x500")
        win.configure(bg='#1e1e1e')
        
        text = scrolledtext.ScrolledText(win, wrap=tk.WORD, font=("Consolas", 10),
                                          bg='#1e1e1e', fg='#d4d4d4')
        text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        status = f"=== 系统状态 ===\n\n"
        status += f"状态变量: {self.agent.sandbox.get_state_keys() or '无'}\n"
        status += f"空闲状态: {'是' if self.agent.is_idle() else '否'}\n"
        status += f"协作模式: {'启用' if ENABLE_ASSISTANT else '禁用'}\n"
        status += f"辅助模型: {'可用' if self.agent.ai.is_assistant_available() else '不可用'}\n\n"
        
        # 协作历史
        collab_history = self.agent.ai.get_collaboration_history()
        if collab_history:
            status += f"\n=== 协作历史 (最近5条) ===\n\n"
            for h in collab_history[-5:]:
                status += f"- {h.get('context', '')[:50]}...\n"
        
        text.insert(tk.END, status)
        text.config(state=tk.DISABLED)
        tk.Button(win, text="关闭", command=win.destroy).pack(pady=10)
    
    def show_collab_status(self):
        """显示协作状态"""
        win = tk.Toplevel(self.root)
        win.title("协作状态")
        win.geometry("700x400")
        win.configure(bg='#1e1e1e')
        
        text = scrolledtext.ScrolledText(win, wrap=tk.WORD, font=("Consolas", 10),
                                          bg='#1e1e1e', fg='#d4d4d4')
        text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        status = "=== 协作模式状态 ===\n\n"
        status += f"协作模式: {'启用' if ENABLE_ASSISTANT else '禁用'}\n"
        status += f"辅助模型可用: {'是' if self.agent.ai.is_assistant_available() else '否'}\n"
        status += f"主模型: {self.agent.ai.main_backend}\n"
        status += f"辅助模型: {self.agent.ai.assistant_backend}\n\n"
        
        # 测试辅助模型
        if ENABLE_ASSISTANT:
            status += "测试辅助模型连接...\n"
            if self.agent.ai.test_assistant_connection():
                status += " 辅助模型连接正常\n\n"
            else:
                status += " 辅助模型连接失败\n\n"
        
        # 协作历史
        history = self.agent.ai.get_collaboration_history()
        status += f"=== 协作历史 ({len(history)} 条) ===\n\n"
        if history:
            for i, h in enumerate(history[-10:], 1):
                status += f"{i}. {h.get('context', '')[:80]}\n"
                status += f"   响应: {h.get('response', '')[:100]}...\n\n"
        else:
            status += "暂无协作记录\n"
        
        text.insert(tk.END, status)
        text.config(state=tk.DISABLED)
        tk.Button(win, text="关闭", command=win.destroy).pack(pady=10)
    
    def run(self):
        self.root.mainloop()
    
    def on_close(self):
        self.heartbeat.stop()
        shutdown_manager()
        if hasattr(self, 'agent') and self.agent:
            self.agent.interrupt()
            self.agent.reset()
            self.agent._save_short_term_memory()
            if hasattr(self.agent, 'ext_manager') and self.agent.ext_manager:
                self.agent.ext_manager.shutdown()
        if hasattr(self.agent, 'db'):
            self.agent.db = None
        if hasattr(self.agent, 'sandbox'):
            self.agent.sandbox.force_cleanup()
        
        import gc
        gc.collect()
        gc.collect()
        self.root.destroy()

# miniagent.py 底部修改

def main():
    import os
    import sys
    
    # ===== 获取 EXE 所在目录（兼容打包） =====
    if getattr(sys, 'frozen', False):
        # 打包成 EXE 后，使用 sys._MEIPASS 获取临时解压目录
        base_dir = os.path.dirname(sys.executable)
        # 数据文件放在 EXE 同目录
        os.chdir(base_dir)
    else:
        # 源码运行，使用当前目录
        base_dir = os.path.dirname(os.path.abspath(__file__))
    
    print("="*60)
    print("AI Agent v9.9 - 双模型协作版本")
    print("="*60)
    
    # 检查配置
    from config_manager import ensure_config
    if not ensure_config():
        print("配置未完成，程序退出")
        input("按 Enter 键退出...")
        return
    
    # 写入 PID（用于看门狗内部检测）
    try:
        with open("agent.pid", "w") as f:
            f.write(str(os.getpid()))
    except:
        pass
    
    # 启动内部看门狗（作为线程）
    from watchdog import WatchdogThread
    watchdog = WatchdogThread(check_interval=60, timeout_minutes=15)
    watchdog.start()
    
    try:
        ui = AgentUI()
        ui.run()
    finally:
        watchdog.stop()


if __name__ == "__main__":
    while True:
        try:
            main()              # 正常运行时，main() 会一直阻塞
        except Exception as e:   # 只有崩溃才会进入这里
            print(f"进程崩溃: {e}，5秒后重启...")
            time.sleep(5)
            continue             # 重启
        break                    # ← main() 正常退出时，执行 break，退出 while 循环
