#!/usr/bin/env python3
"""
AI 客户端 - 支持 Ollama、通义千问、DeepSeek 和智谱
支持双模型协作模式
"""

import requests
from typing import Dict, List, Optional, Callable, Any
import json
import threading
from simple_logger import log


import os

CONFIG_FILE = "ai_config.json"


# ============================================================================
# 配置
# ============================================================================

# AI 后端选择: "ollama" 或 "dashscope" 或 "deepseek" 或 "zhipu" or  "tokenhub"
# AI_BACKEND = "ollama"

# 主模型配置（负责与脚本交互）
# MAIN_BACKEND = "ollama"

# 辅助模型配置（负责协作）
# ASSISTANT_BACKEND = "zhipu"  # 本地模型

# Ollama 配置
# OLLAMA_URL = "http://localhost:11434/api/chat"
# OLLAMA_MODEL = "qwen2.5:7b"  #  qwen3:8b   / qwen2.5:7b / qwen3.5:4b / gemma4:e4b
# OLLAMA_MAX_TOKENS = 8192

# 通义千问配置（DashScope）
# DASHSCOPE_API_KEY = "DASHSCOPE_API_KEY"
# DASHSCOPE_MODEL = "qwen3.7-max-2026-06-08"   # kimi-k2.6优秀
# DASHSCOPE_MAX_TOKENS = 8192

# DeepSeek 配置
# DEEPSEEK_API_KEY = "DEEPSEEK_API_KEY"
# DEEPSEEK_MODEL = "deepseek-v4-flash" #  deepseek-v4-pro   deepseek-v4-flash
# DEEPSEEK_MAX_TOKENS = 8192

# 智谱 AI 配置 (GLM-4.7-Flash)
# ZHIPU_API_KEY = "ZHIPU_API_KEY"  # 请替换为你的智谱 API Key
# ZHIPU_MODEL = "glm-4.5-flash"  # 免费模型 glm-4.7-flash  glm-4.5-flash   glm-4.5-air
# ZHIPU_MAX_TOKENS = 8192
# ZHIPU_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"

# ============================================================================
# 腾讯 TokenHub 配置
# ============================================================================


# TOKENHUB_API_KEY = "TOKENHUB_API_KEYV"
# TOKENHUB_MODEL = "hy3-preview"  # 或 hunyuan-pro, hunyuan-lite, glm-4 等
# TOKENHUB_MAX_TOKENS = 8192
# TOKENHUB_BASE_URL = "https://tokenhub.tencentmaas.com/v1"  # 官方地址


# 通用配置
# TEMPERATURE = 0.3
# TIMEOUT = 120

# ============================================================================
# 自省学习配置（Agent 空闲时自动学习）
# ============================================================================

IDLE_THRESHOLD = 60
LEARNING_INTERVAL = 300
MAX_LEARNING_ROUNDS = 3



def load_config() -> dict:
    """加载配置文件，如果不存在则返回空字典"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"加载配置失败: {e}")
            return {}
    return {}



def reload_config():
    """重新加载配置文件并更新全局变量"""
    global OLLAMA_URL, OLLAMA_MODEL, OLLAMA_MAX_TOKENS
    global DASHSCOPE_API_KEY, DASHSCOPE_MODEL, DASHSCOPE_MAX_TOKENS
    global DEEPSEEK_API_KEY, DEEPSEEK_MODEL, DEEPSEEK_MAX_TOKENS
    global ZHIPU_API_KEY, ZHIPU_MODEL, ZHIPU_MAX_TOKENS, ZHIPU_URL
    global TOKENHUB_API_KEY, TOKENHUB_MODEL, TOKENHUB_MAX_TOKENS, TOKENHUB_BASE_URL
    global TEMPERATURE, TIMEOUT, MAIN_BACKEND, ASSISTANT_BACKEND
    
    config = load_config()
    if not config:
        print("[配置] 配置文件不存在，使用默认值")
        return

    
    ollama = config.get("ollama", {})
    OLLAMA_URL = ollama.get("url", "http://localhost:11434/api/chat")
    OLLAMA_MODEL = ollama.get("model", "qwen2.5:7b")
    OLLAMA_MAX_TOKENS = ollama.get("max_tokens", 8192)
    
    dash = config.get("dashscope", {})
    DASHSCOPE_API_KEY = dash.get("api_key", "")
    DASHSCOPE_MODEL = dash.get("model", "qwen3.7-max-2026-06-08")
    DASHSCOPE_MAX_TOKENS = dash.get("max_tokens", 8192)
    
    deep = config.get("deepseek", {})
    DEEPSEEK_API_KEY = deep.get("api_key", "")
    DEEPSEEK_MODEL = deep.get("model", "deepseek-v4-flash")
    DEEPSEEK_MAX_TOKENS = deep.get("max_tokens", 8192)
    
    zhipu = config.get("zhipu", {})
    ZHIPU_API_KEY = zhipu.get("api_key", "")
    ZHIPU_MODEL = zhipu.get("model", "glm-4.5-flash")
    ZHIPU_MAX_TOKENS = zhipu.get("max_tokens", 8192)
    ZHIPU_URL = zhipu.get("url", "https://open.bigmodel.cn/api/paas/v4/chat/completions")
    
    token = config.get("tokenhub", {})
    TOKENHUB_API_KEY = token.get("api_key", "")
    TOKENHUB_MODEL = token.get("model", "hy3-preview")
    TOKENHUB_MAX_TOKENS = token.get("max_tokens", 8192)
    TOKENHUB_BASE_URL = token.get("base_url", "https://tokenhub.tencentmaas.com/v1")
    
    gen = config.get("general", {})
    TEMPERATURE = gen.get("temperature", 0.3)
    TIMEOUT = gen.get("timeout", 120)
    MAIN_BACKEND = gen.get("main_backend", "ollama")
    ASSISTANT_BACKEND = gen.get("assistant_backend", "zhipu")
    IDLE_THRESHOLD = gen.get("idle_threshold", 60)
    LEARNING_INTERVAL = gen.get("learning_interval", 300)
    
    print("[配置] 已重新加载")


reload_config()

# ============================================================================
# Ollama 客户端
# ============================================================================

class OllamaClient:
    def __init__(self, url: str = OLLAMA_URL, model: str = OLLAMA_MODEL):
        self.url = url
        self.model = model
        self.max_tokens = OLLAMA_MAX_TOKENS
        self.temperature = TEMPERATURE
        self.timeout = TIMEOUT
    
    def chat(self, messages: List[Dict]) -> Optional[str]:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens
            },
            "enable_thinking": False
        }
        
        print(f"[DEBUG] 实际使用模型: '{self.model}'")
        try:
            print(f"[TokenHub] 请求URL:{self.url}")
            print(f"[TokenHub] 模型: {self.model}")
            resp = requests.post(self.url, json=payload, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json().get("message", {}).get("content", "")
        except Exception as e:
            print(f"Ollama API 错误: {e}")
            return None
    
    def test_connection(self) -> bool:
        try:
            resp = requests.get("http://localhost:11434/api/tags", timeout=10)
            return resp.status_code == 200
        except:
            return False


# ============================================================================
# 通义千问客户端（DashScope）
# ============================================================================

class DashScopeClient:
    def __init__(self):
        self.api_key = DASHSCOPE_API_KEY
        self.model = DASHSCOPE_MODEL
        self.max_tokens = DASHSCOPE_MAX_TOKENS
        self.temperature = TEMPERATURE
        self.timeout = TIMEOUT
        self.url = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    
    def chat(self, messages: List[Dict]) -> Optional[str]:
        formatted_messages = []
        for msg in messages:
            formatted_messages.append({
                "role": msg["role"],
                "content": msg["content"]
            })
        
        payload = {
            "model": self.model,
            "messages": formatted_messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens
        }
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        try:
            print(f"[TokenHub] 请求URL:{self.url}")
            print(f"[TokenHub] 模型: {self.model}")
            resp = requests.post(self.url, json=payload, headers=headers, timeout=self.timeout)
            resp.raise_for_status()
            result = resp.json()
            return result["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"通义千问 API 错误: {e}")
            return None
    
    def test_connection(self) -> bool:
        try:
            resp = self.chat([{"role": "user", "content": "测试"}])
            return resp is not None
        except:
            return False


# ============================================================================
# DeepSeek 客户端
# ============================================================================

class DeepSeekClient:
    def __init__(self):
        self.api_key = DEEPSEEK_API_KEY
        self.model = DEEPSEEK_MODEL
        self.max_tokens = DEEPSEEK_MAX_TOKENS
        self.temperature = TEMPERATURE
        self.timeout = TIMEOUT
        self.base_url = "https://api.deepseek.com/v1"
    
    def chat(self, messages: List[Dict]) -> Optional[str]:
        formatted_messages = []
        for msg in messages:
            formatted_messages.append({
                "role": msg["role"],
                "content": msg["content"]
            })
        
        payload = {
            "model": self.model,
            "messages": formatted_messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False
        }
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        try:
            print(f"[TokenHub] 请求URL:{self.base_url}/chat/completions")
            print(f"[TokenHub] 模型: {self.model}")

            resp = requests.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=headers,
                timeout=self.timeout
            )
            resp.raise_for_status()
            result = resp.json()
            return result["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"DeepSeek API 错误: {e}")
            return None
    
    def test_connection(self) -> bool:
        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            resp = requests.get(
                "https://api.deepseek.com/v1/models",
                headers=headers,
                timeout=10
            )
            return resp.status_code == 200
        except:
            return False

# ============================================================================
# 智谱 AI 客户端 (GLM-4.7-Flash) - 不依赖 PyJWT
# ============================================================================

class ZhipuClient:
    def __init__(self):
        self.api_key = ZHIPU_API_KEY
        self.model = ZHIPU_MODEL
        self.max_tokens = ZHIPU_MAX_TOKENS
        self.temperature = TEMPERATURE
        self.timeout = TIMEOUT
        self.url = ZHIPU_URL
    
    def chat(self, messages: List[Dict]) -> Optional[str]:
        formatted_messages = []
        for msg in messages:
            formatted_messages.append({
                "role": msg["role"],
                "content": msg["content"]
            })
        
        payload = {
            "model": self.model,
            "messages": formatted_messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
            "thinking": {"type": "disabled"}
        }
        
        # 智谱 API 使用 Bearer Token 认证（API Key 直接作为 token）
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
            
        
        try:
            
            print(f"[TokenHub] 请求URL: {self.url}")
            print(f"[TokenHub] 模型: {self.model}")
            resp = requests.post(self.url, json=payload, headers=headers, timeout=self.timeout)
            resp.raise_for_status()
            result = resp.json()
            return result["choices"][0]["message"]["content"]
        except requests.exceptions.RequestException as e:
            print(f"智谱 API 请求错误: {e}")
            if hasattr(e, 'response') and e.response:
                print(f"响应内容: {e.response.text}")
            return None
        except KeyError as e:
            print(f"智谱 API 响应解析错误: {e}")
            return None
        except Exception as e:
            print(f"智谱 API 未知错误: {e}")
            return None
    
    def test_connection(self) -> bool:
        try:
            resp = self.chat([{"role": "user", "content": "请回复'连接成功'"}])
            return resp is not None
        except:
            return False

# ============================================================================
# 腾讯 TokenHub 客户端（基于官方模板）
# ============================================================================

class TokenHubClient:
    """
    腾讯 TokenHub 客户端
    使用 OpenAI 兼容接口
    """
    
    def __init__(self):
        self.api_key = TOKENHUB_API_KEY
        self.model = TOKENHUB_MODEL  # "hy3"
        self.max_tokens = TOKENHUB_MAX_TOKENS
        self.temperature = TEMPERATURE
        self.timeout = TIMEOUT
        self.base_url = TOKENHUB_BASE_URL  # "https://tokenhub.tencentmaas.com/v1"
    
    def chat(self, messages: List[Dict]) -> Optional[str]:
        """
        调用 TokenHub API 进行对话
        """
        # 格式化消息
        formatted_messages = []
        for msg in messages:
            formatted_messages.append({
                "role": msg["role"],
                "content": msg["content"]
            })
        
        # 根据腾讯模板，payload 格式与 OpenAI 完全一致
        payload = {
            "model": self.model,
            "messages": formatted_messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False
        }
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        try:
            # 注意：base_url 已经包含 /v1，所以直接拼接 /chat/completions
            # 结果是 https://tokenhub.tencentmaas.com/v1/chat/completions
            url = f"{self.base_url}/chat/completions"
            
            print(f"[TokenHub] 请求URL: {url}")
            print(f"[TokenHub] 模型: {self.model}")
            
            resp = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=self.timeout
            )
            
            # 打印调试信息
            print(f"[TokenHub] 响应状态码: {resp.status_code}")
            if resp.status_code != 200:
                print(f"[TokenHub] 错误响应: {resp.text}")
            
            resp.raise_for_status()
            result = resp.json()
            return result["choices"][0]["message"]["content"]
            
        except requests.exceptions.RequestException as e:
            print(f"TokenHub API 请求错误: {e}")
            if hasattr(e, 'response') and e.response:
                print(f"响应内容: {e.response.text}")
            return None
        except KeyError as e:
            print(f"TokenHub API 响应解析错误: {e}")
            return None
        except Exception as e:
            print(f"TokenHub API 未知错误: {e}")
            return None
    
    def test_connection(self) -> bool:
        """测试 TokenHub 连接"""
        try:
            resp = self.chat([{"role": "user", "content": "请回复'连接成功'"}])
            return resp is not None and len(resp) > 0
        except:
            return False


# ============================================================================
# 统一 AI 客户端
# ============================================================================

class AIClient:
    def __init__(self, backend: str = None, stats_callback=None):
        if backend is None:
            backend = MAIN_BACKEND
        self.backend = backend
        self.client = self._create_client(backend)
        self.stats_callback = stats_callback
        self._chat_lock = threading.Lock()
    
    def _create_client(self, backend: str):
        if backend == "ollama":
            return OllamaClient()
        elif backend == "dashscope":
            return DashScopeClient()
        elif backend == "deepseek":
            return DeepSeekClient()
        elif backend == "zhipu":
            return ZhipuClient()
        elif backend == "tokenhub":
            return TokenHubClient()
        else:
            raise ValueError(f"Unknown backend: {backend}")
    
    def chat(self, messages: List[Dict]) -> Optional[str]:
        # ===== 记录发送的请求 =====
        user_msg = ""
        for msg in messages:
            if msg.get("role") == "user":
                user_msg = msg.get("content", "")
                break
        print(f"[DEBUG] 当前后台：{self.backend}")
        print(f"[DEBUG] 即将记录请求, len={len(user_msg)}")
        log(f"[AI请求] {user_msg}")
        
        total_chars = len(str(messages))
        if self.stats_callback:
            self.stats_callback(f"[发送] {total_chars} 字符")
        
        try:
            with self._chat_lock: 
                response = self.client.chat(messages)

            if response:
                print(f"[DEBUG] 即将记录响应, len={len(response)}")
                log(f"[AI响应] {response}")
            else:
                print("[DEBUG] 响应为空")
                log(f"[AI响应] 返回为空")
            
            return response
            
        except Exception as e:
            print(f"[DEBUG] 即将记录错误: {e}")
            log(f"[AI错误] {type(e).__name__}: {str(e)}")
            raise
    
    def test_connection(self) -> bool:
        return self.client.test_connection()


# ============================================================================
# 协作 AI 客户端（支持双模型协作）
# ============================================================================

class CollaborativeAIClient:
    """
    双模型协作客户端
    - 主模型：负责与脚本交互，处理指令
    - 辅助模型：作为顾问，处理主模型分配的子任务
    """
    
    def __init__(self, 
                 main_backend: str = None,
                 assistant_backend: str = None,
                 stats_callback=None):
        """
        初始化协作客户端
        
        Args:
            main_backend: 主模型后端 (ollama/dashscope/deepseek/zhipu)
            assistant_backend: 辅助模型后端 (ollama/dashscope/deepseek/zhipu)
            stats_callback: 状态回调
        """
        self.main_backend = main_backend or MAIN_BACKEND
        self.assistant_backend = assistant_backend or ASSISTANT_BACKEND
        
        self.main_client = AIClient(self.main_backend, stats_callback)
        self.assistant_client = AIClient(self.assistant_backend, stats_callback)
        
        self.stats_callback = stats_callback
        self._lock = threading.RLock()
        
        # 协作历史记录
        self.collaboration_history: List[Dict] = []
        self.MAX_COLLAB_HISTORY = 50
        
        # 辅助模型是否可用
        self._assistant_available = None
    
    def get_main_client(self) -> AIClient:
        """获取主模型客户端"""
        return self.main_client
    
    def get_assistant_client(self) -> AIClient:
        """获取辅助模型客户端"""
        return self.assistant_client
    
    def chat(self, messages: List[Dict]) -> Optional[str]:
        """使用主模型进行对话（对外接口）"""
        return self.main_client.chat(messages)
    
    def chat_with_assistant(self, messages: List[Dict], 
                            context: str = "") -> Optional[str]:
        """
        使用辅助模型进行对话
        
        Args:
            messages: 消息列表
            context: 上下文描述（用于记录协作历史）
        """
        with self._lock:
            if self._assistant_available is None:
                self._assistant_available = self.assistant_client.test_connection()
            
            if not self._assistant_available:
                if self.stats_callback:
                    self.stats_callback("[辅助模型] 不可用")
                return None
            
            response = self.assistant_client.chat(messages)
            
            if response and context:
                # 记录协作历史
                self.collaboration_history.append({
                    "timestamp": __import__('time').time(),
                    "context": context,
                    "request": str(messages)[:500],
                    "response": response[:500]
                })
                # 限制历史长度
                if len(self.collaboration_history) > self.MAX_COLLAB_HISTORY:
                    self.collaboration_history = self.collaboration_history[-self.MAX_COLLAB_HISTORY:]
            
            return response
    
    def request_assistant_help(self, task: str, 
                               context: Dict = None,
                               max_retries: int = 2) -> Optional[str]:
        """
        向辅助模型请求帮助（便捷方法）
        
        Args:
            task: 任务描述
            context: 上下文信息
            max_retries: 最大重试次数
        """
        context_str = json.dumps(context, ensure_ascii=False) if context else "无"
        
        prompt = f"""你是 AI 助手的辅助模型，请帮助处理以下子任务。

## 主任务上下文
{context_str}

## 子任务
{task}

## 要求
1. 直接给出处理结果，不要过多解释
2. 如果是代码，使用纯文本格式，不要用 Markdown 代码块
3. 输出应该简洁、专业
4. 只输出结果内容，不要包含 "这是结果" 等前缀

请处理这个子任务："""
        
        messages = [
            {"role": "system", "content": "你是AI助手的高级辅助模型，专长于代码审查、数据分析、文本处理等子任务。你给出的结果会被主模型直接使用。"},
            {"role": "user", "content": prompt}
        ]
        
        for attempt in range(max_retries):
            response = self.chat_with_assistant(messages, f"任务: {task[:50]}...")
            if response:
                return response
            if self.stats_callback:
                self.stats_callback(f"[辅助模型] 重试 {attempt + 1}/{max_retries}")
        
        return None
    
    def is_assistant_available(self) -> bool:
        """检查辅助模型是否可用"""
        if self._assistant_available is None:
            self._assistant_available = self.assistant_client.test_connection()
        return self._assistant_available
    
    def get_collaboration_history(self) -> List[Dict]:
        """获取协作历史"""
        with self._lock:
            return self.collaboration_history.copy()
    
    def clear_collaboration_history(self):
        """清空协作历史"""
        with self._lock:
            self.collaboration_history.clear()
    
    def test_connection(self) -> bool:
        """测试主模型连接"""
        return self.main_client.test_connection()
    
    def test_assistant_connection(self) -> bool:
        """测试辅助模型连接"""
        return self.assistant_client.test_connection()


# ============================================================================
# 工厂函数：创建客户端
# ============================================================================

def create_ai_client(backend: str = None, stats_callback=None) -> AIClient:
    """创建标准 AI 客户端"""
    return AIClient(backend, stats_callback)


def create_collaborative_client(main_backend: str = None,
                                assistant_backend: str = None,
                                stats_callback=None) -> CollaborativeAIClient:
    """创建协作 AI 客户端"""
    return CollaborativeAIClient(main_backend, assistant_backend, stats_callback)




# 在 ai_client.py 末尾添加
__all__ = [
    'AIClient',
    'CollaborativeAIClient',
    'create_ai_client',
    'create_collaborative_client',
    'reload_config',
    'IDLE_THRESHOLD',
    'LEARNING_INTERVAL',
    'MAX_LEARNING_ROUNDS',
]
