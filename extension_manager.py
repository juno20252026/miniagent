#!/usr/bin/env python3
"""
扩展管理类 - 为AI Agent提供扩展能力管理
支持通过统一的 EXTENSION_MANAGE 指令进行增删查改操作
"""

import os
import sys
import json
import sqlite3
import importlib.util
import hashlib
import shutil
import subprocess
import threading
import queue
import signal
import re
import time
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
from concurrent.futures import ThreadPoolExecutor
import warnings
warnings.filterwarnings('ignore')


# ============================================================================
# 扩展数据库操作
# ============================================================================

class ExtensionDB:
    """扩展能力数据库操作"""
    
    def __init__(self, db_path: str = "agent_memory.db"):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """初始化扩展表"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS extensions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    description TEXT NOT NULL,
                    usage_guide TEXT,
                    script_path TEXT NOT NULL,
                    entry_point TEXT DEFAULT 'run',
                    import_count INTEGER DEFAULT 0,
                    last_used_at DATETIME,
                    status TEXT DEFAULT 'active',
                    version TEXT DEFAULT '1.0.0',
                    author TEXT,
                    dependencies TEXT,
                    timeout INTEGER DEFAULT 30,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    metadata TEXT
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS extension_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    extension_id INTEGER NOT NULL,
                    version TEXT NOT NULL,
                    script_path TEXT NOT NULL,
                    changelog TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (extension_id) REFERENCES extensions(id) ON DELETE CASCADE
                )
            """)
    
    def _query(self, sql: str, params: tuple = (), fetch_one: bool = False) -> Any:
        """通用查询"""
        with sqlite3.connect(self.db_path, timeout=5.0) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(sql, params)
            if fetch_one:
                row = cursor.fetchone()
                return dict(row) if row else None
            return [dict(row) for row in cursor.fetchall()]
    
    def _execute(self, sql: str, params: tuple = ()) -> int:
        """通用执行"""
        with sqlite3.connect(self.db_path, timeout=5.0) as conn:
            cursor = conn.execute(sql, params)
            conn.commit()
            return cursor.lastrowid
    
    # ========== 扩展CRUD操作 ==========
    
    def add_extension(self, name: str, description: str, script_path: str,
                      entry_point: str = 'run', author: str = '',
                      version: str = '1.0.0', dependencies: List[str] = None,
                      usage_guide: str = '', timeout: int = 30,
                      metadata: Dict = None) -> int:
        """添加新扩展"""
        deps_json = json.dumps(dependencies or [])
        meta_json = json.dumps(metadata or {})
        return self._execute("""
            INSERT INTO extensions 
            (name, description, usage_guide, script_path, entry_point, 
             author, version, dependencies, timeout, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (name, description, usage_guide, script_path, entry_point,
              author, version, deps_json, timeout, meta_json))
    
    def get_extension(self, extension_id: int) -> Optional[Dict]:
        return self._query(
            "SELECT * FROM extensions WHERE id = ?",
            (extension_id,), fetch_one=True
        )
    
    def get_extension_by_name(self, name: str) -> Optional[Dict]:
        return self._query(
            "SELECT * FROM extensions WHERE name = ?",
            (name,), fetch_one=True
        )
    
    def get_extensions(self, status: str = None, limit: int = 50) -> List[Dict]:
        """获取扩展列表"""
        if status:
            return self._query(
                "SELECT * FROM extensions WHERE status = ? ORDER BY import_count DESC, created_at DESC LIMIT ?",
                (status, limit)
            )
        return self._query(
            "SELECT * FROM extensions ORDER BY import_count DESC, created_at DESC LIMIT ?",
            (limit,)
        )
    
    def get_active_extensions(self, limit: int = 20) -> List[Dict]:
        return self._query("""
            SELECT * FROM extensions 
            WHERE status = 'active' 
            ORDER BY import_count DESC, last_used_at DESC 
            LIMIT ?
        """, (limit,))
    
    def update_extension(self, extension_id: int, **kwargs) -> bool:
        """更新扩展信息"""
        allowed_fields = ['name', 'description', 'usage_guide', 'script_path',
                         'entry_point', 'status', 'version', 'author', 
                         'timeout', 'dependencies', 'metadata']
        updates = []
        params = []
        for key, value in kwargs.items():
            if key in allowed_fields:
                if key in ['dependencies', 'metadata']:
                    value = json.dumps(value) if value else '{}'
                updates.append(f"{key} = ?")
                params.append(value)
        if not updates:
            return False
        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(extension_id)
        self._execute(
            f"UPDATE extensions SET {', '.join(updates)} WHERE id = ?",
            tuple(params)
        )
        return True
    
    def update_extension_stats(self, extension_id: int):
        """更新使用统计"""
        self._execute("""
            UPDATE extensions 
            SET import_count = import_count + 1, last_used_at = CURRENT_TIMESTAMP 
            WHERE id = ?
        """, (extension_id,))
    
    def delete_extension(self, extension_id: int) -> bool:
        """删除扩展（软删除：状态设为disabled）"""
        return self.update_extension(extension_id, status='disabled')
    
    def permanently_delete(self, extension_id: int) -> bool:
        """永久删除扩展"""
        self._execute("DELETE FROM extensions WHERE id = ?", (extension_id,))
        return True
    
    def search_extensions(self, query: str, limit: int = 10) -> List[Dict]:
        """搜索扩展"""
        keywords = re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z]{2,}', query)
        if not keywords:
            return []
        
        conditions = []
        params = []
        for kw in keywords[:3]:
            conditions.append("name LIKE ? OR description LIKE ? OR usage_guide LIKE ?")
            params.extend([f"%{kw}%", f"%{kw}%", f"%{kw}%"])
        
        return self._query(
            f"SELECT * FROM extensions WHERE ({' OR '.join(conditions)}) AND status = 'active' ORDER BY import_count DESC LIMIT ?",
            tuple(params + [limit])
        )
    
    # ========== 版本管理 ==========
    
    def add_version_history(self, extension_id: int, version: str, 
                            script_path: str, changelog: str = "") -> int:
        """添加版本历史记录"""
        return self._execute("""
            INSERT INTO extension_versions (extension_id, version, script_path, changelog)
            VALUES (?, ?, ?, ?)
        """, (extension_id, version, script_path, changelog))
    
    def get_version_history(self, extension_id: int, limit: int = 20) -> List[Dict]:
        return self._query("""
            SELECT * FROM extension_versions 
            WHERE extension_id = ? 
            ORDER BY created_at DESC LIMIT ?
        """, (extension_id, limit))
    
    def get_version_by_id(self, version_id: int) -> Optional[Dict]:
        return self._query(
            "SELECT * FROM extension_versions WHERE id = ?",
            (version_id,), fetch_one=True
        )


# ============================================================================
# 路径验证器
# ============================================================================

class PathValidator:
    """路径安全验证器"""
    
    ALLOWED_ROOTS = [
        Path("./extensions").resolve(),
        Path("./Dworkspace").resolve(),
        Path("./tools").resolve(),
        Path("./sandbox_temp").resolve(),
    ]
    
    FORBIDDEN_PATTERNS = [
        r'\.\./', r'\.\.\\',
        r'/proc/', r'/sys/', r'/etc/',
        r'C:\\Windows', r'C:\\Program Files',
    ]
    
    @classmethod
    def validate_path(cls, script_path: str) -> Tuple[bool, str, Optional[Path]]:
        """验证路径是否安全 - 扩展脚本必须在 ./extensions 目录下"""
        if not script_path or not script_path.strip():
            return False, "路径不能为空", None
        
        # 只允许 .py 文件
        if not script_path.endswith('.py'):
            return False, f"不支持的文件类型: {script_path}", None
        
        # 禁止路径遍历
        if '..' in script_path or '/' in script_path.replace('\\', '/'):
            # 只允许文件名，不允许路径
            filename = Path(script_path).name
            if filename != script_path and not script_path.startswith('extensions'):
                return False, f"路径包含非法字符: {script_path}", None
        
        # 提取文件名
        filename = Path(script_path).name
        
        # 构建完整路径（固定到 extensions 目录）
        p = Path.cwd() / "extensions" / filename
        abs_path = p.resolve()
        
        # 确保文件存在
        if not abs_path.exists():
            return False, f"文件不存在: {abs_path}", None
        if not abs_path.is_file():
            return False, f"不是有效文件: {abs_path}", None
        
        file_size = abs_path.stat().st_size
        if file_size > 10 * 1024 * 1024:
            return False, f"文件过大: {file_size} bytes", None
        
        return True, "验证通过", abs_path

    @classmethod
    def add_allowed_root(cls, path: str):
        cls.ALLOWED_ROOTS.append(Path(path))


# ============================================================================
# 导入防护
# ============================================================================

class ImportGuard:
    """防止循环导入和危险导入"""
    
    def __init__(self):
        self._importing_extensions = set()
        self._extension_import_depth = {}
        self._lock = threading.RLock()
        
        self.FORBIDDEN_IMPORTS = {
            'miniagent', 'agent', 'AIAgent',
            'extension_manager', 'ExtensionLoader', 'ExtensionManager'
        }
        
        self.MONITORED_PREFIXES = ['extension_', 'ext_']
    
    def guard_import(self, extension_name: str):
        """上下文管理器：防护导入"""
        lock = self._lock
        importing_extensions = self._importing_extensions
        extension_import_depth = self._extension_import_depth
        
        class GuardContext:
            def __enter__(self):
                with lock:
                    if extension_name in importing_extensions:
                        raise ImportError(f"检测到循环导入: {extension_name}")
                    
                    depth = extension_import_depth.get(extension_name, 0)
                    if depth > 5:
                        raise ImportError(f"导入深度超过限制: {extension_name}")
                    
                    importing_extensions.add(extension_name)
                    extension_import_depth[extension_name] = depth + 1
                    return self
            
            def __exit__(self, exc_type, exc_val, exc_tb):
                with lock:
                    importing_extensions.discard(extension_name)
                    if extension_name in extension_import_depth:
                        extension_import_depth[extension_name] -= 1
                        if extension_import_depth[extension_name] <= 0:
                            del extension_import_depth[extension_name]
        return GuardContext()
    
    def is_forbidden_import(self, module_name: str) -> bool:
        if module_name in self.FORBIDDEN_IMPORTS:
            return True
        for prefix in self.MONITORED_PREFIXES:
            if module_name.startswith(prefix):
                return False
        return False
    
    def create_safe_import_hook(self):
        """创建安全的导入钩子"""
        original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__
        
        def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
            if self.is_forbidden_import(name):
                raise ImportError(f"禁止导入模块: {name}")
            
            frame = sys._getframe(1)
            if frame and frame.f_globals:
                module_name = frame.f_globals.get('__name__', '')
                if module_name.startswith('extension_') or module_name.startswith('ext_'):
                    if name == '__main__' or name.startswith('miniagent'):
                        raise ImportError(f"扩展模块禁止导入主程序: {name}")
            
            return original_import(name, globals, locals, fromlist, level)
        
        return safe_import


# ============================================================================
# 依赖检查器
# ============================================================================

class DependencyChecker:
    """依赖检查器"""
    
    @staticmethod
    def check_dependencies(dependencies: List[str]) -> Tuple[bool, List[str]]:
        """检查依赖是否满足"""
        if not dependencies:
            return True, []
        
        missing = []
        try:
            import pkg_resources
            for dep in dependencies:
                try:
                    if '==' in dep:
                        pkg, ver = dep.split('==', 1)
                        pkg_resources.require(f"{pkg}=={ver}")
                    elif '>=' in dep:
                        pkg, ver = dep.split('>=', 1)
                        pkg_resources.require(f"{pkg}>={ver}")
                    else:
                        pkg_resources.require(dep)
                except Exception as e:
                    missing.append(str(e))
        except ImportError:
            pass
        
        return len(missing) == 0, missing
    
    @staticmethod
    def auto_install_dependencies(dependencies: List[str], 
                                  timeout: int = 60) -> Tuple[bool, List[str]]:
        """自动安装缺失的依赖"""
        success, missing = DependencyChecker.check_dependencies(dependencies)
        if success:
            return True, []
        
        installed = []
        for dep in missing:
            try:
                package_name = dep.split('==')[0].split('>=')[0].split('>')[0].strip()
                subprocess.run(
                    [sys.executable, '-m', 'pip', 'install', package_name,
                     '--index-url', 'https://mirrors.aliyun.com/pypi/simple/'],
                    timeout=timeout,
                    capture_output=True,
                    check=True
                )
                installed.append(package_name)
            except Exception as e:
                return False, [f"安装失败: {dep} - {e}"]
        
        return True, installed


# ============================================================================
# 扩展隔离执行器
# ============================================================================

class ExtensionIsolation:
    """扩展隔离执行器"""
    
    def __init__(self):
        self._executor = ThreadPoolExecutor(max_workers=10)
    
    def execute_with_isolation(self, func, params: Dict, 
                                timeout: int = 30) -> Dict:
        """在隔离线程中执行扩展"""
        result_queue = queue.Queue()
        
        def wrapper():
            try:
                # 直接执行，不处理 signal
                result = func(**params)
                result_queue.put({
                    'status': 'success',
                    'result': result,
                    'error': None
                })
            except Exception as e:
                result_queue.put({
                    'status': 'error',
                    'result': None,
                    'error': str(e),
                    'error_type': type(e).__name__
                })
        
        future = self._executor.submit(wrapper)
        
        try:
            result = result_queue.get(timeout=timeout)
            return result
        except queue.Empty:
            future.cancel()
            return {
                'status': 'timeout',
                'result': None,
                'error': f'执行超时（>{timeout}秒）'
            }
        except Exception as e:
            return {
                'status': 'error',
                'result': None,
                'error': f'执行异常: {e}'
            }
    
    def shutdown(self):
        """关闭执行器"""
        self._executor.shutdown(wait=False)


# ============================================================================
# 扩展管理器 - 核心类
# ============================================================================

class ExtensionManager:
    """
    扩展管理器 - 为AI提供完整的扩展管理能力
    
    支持的操作:
    - list: 列出所有扩展
    - get: 获取扩展详情
    - search: 搜索扩展
    - add: 添加新扩展
    - update: 更新扩展代码
    - update_info: 更新扩展信息
    - delete: 删除扩展
    - rollback: 回滚到历史版本
    - history: 查看版本历史
    - call: 调用扩展
    """
    
    def __init__(self, db_path: str = "agent_memory.db", logger=None):
        self.db = ExtensionDB(db_path)
        self.logger = logger or print
        
        self.path_validator = PathValidator()
        self.import_guard = ImportGuard()
        self.isolation = ExtensionIsolation()
        
        self._loaded_extensions = {}
        self._module_cache = {}
        self._extensions_dir = Path("./extensions")
        self._extensions_dir.mkdir(parents=True, exist_ok=True)
        
        self._versions_dir = self._extensions_dir / 'versions'
        self._versions_dir.mkdir(parents=True, exist_ok=True)
    
    def log(self, msg: str, level: str = "INFO"):
        """日志输出"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.logger(f"[{timestamp}] [{level}] {msg}")
    
    # ========== 核心操作方法 ==========
    
    def execute_manage(self, payload: Dict) -> str:
        """
        执行扩展管理指令
        
        Args:
            payload: 指令参数
                operation: list|get|search|add|update|update_info|delete|rollback|history|call
        
        Returns:
            str: 操作结果文本
        """
        operation = payload.get("operation", "")
        
        handlers = {
            "list": self._handle_list,
            "get": self._handle_get,
            "search": self._handle_search,
            "add": self._handle_add,
            "update": self._handle_update,
            "update_info": self._handle_update_info,
            "delete": self._handle_delete,
            "rollback": self._handle_rollback,
            "history": self._handle_history,
            "call": self._handle_call,
        }
        
        handler = handlers.get(operation)
        if not handler:
            return f"未知的操作类型: {operation}，支持: {', '.join(handlers.keys())}"
        
        try:
            return handler(payload)
        except Exception as e:
            self.log(f"操作失败: {e}", "ERROR")
            return f"操作失败: {e}"
    
    # ========== 操作方法实现 ==========
    
    def _handle_list(self, payload: Dict) -> str:
        """列出扩展"""
        status = payload.get("status", "active")
        limit = payload.get("limit", 50)
        
        if status == "all":
            extensions = self.db.get_extensions(limit=limit)
        else:
            extensions = self.db.get_extensions(status=status, limit=limit)
        
        if not extensions:
            return f"暂无扩展 (状态: {status})"
        
        lines = [f"扩展列表 (共 {len(extensions)} 个):", ""]
        lines.append(f"{'ID':<6} {'名称':<20} {'版本':<10} {'状态':<10} {'导入次数':<8} {'最后使用'}")
        lines.append("-" * 80)
        
        for ext in extensions:
            last_used = ext.get('last_used_at', '')[:16] if ext.get('last_used_at') else '从未'
            lines.append(
                f"{ext['id']:<6} {ext['name']:<20} {ext['version']:<10} "
                f"{ext['status']:<10} {ext['import_count']:<8} {last_used}"
            )
        
        return "\n".join(lines)
    
    def _handle_get(self, payload: Dict) -> str:
        """获取扩展详情"""
        ext_id = payload.get("extension_id")
        ext_name = payload.get("extension_name")
        
        if not ext_id and not ext_name:
            return "请指定 extension_id 或 extension_name"
        
        if ext_name and not ext_id:
            ext = self.db.get_extension_by_name(ext_name)
            if ext:
                ext_id = ext['id']
        
        if not ext_id:
            return f"未找到扩展: {ext_name}"
        
        try:
            info = self.get_extension_info(ext_id)
        except sqlite3.OperationalError as e:
            return f"数据库操作超时或失败: {e}"
        if 'error' in info:
            return info['error']
        
        lines = [
            f"扩展详情: {info['name']}",
            "=" * 60,
            f"ID: {info['id']}",
            f"名称: {info['name']}",
            f"描述: {info['description']}",
            f"版本: {info['version']}",
            f"状态: {info['status']}",
            f"作者: {info.get('author', '未指定')}",
            f"入口函数: {info['entry_point']}",
            f"导入次数: {info['import_count']}",
            f"最后使用: {info['last_used_at'] or '从未'}",
            f"创建时间: {info['created_at']}",
            f"更新时间: {info['updated_at']}",
            f"脚本路径: {info['script_path']}",
            f"脚本存在: {'是' if info.get('script_exists') else '否'}",
            f"已加载: {'是' if info.get('is_loaded') else '否'}",
            f"超时时间: {info.get('timeout', 30)}秒",
            f"依赖: {', '.join(info.get('dependencies', [])) or '无'}",
        ]


        # ===== 新增：默认显示代码 =====
        script_path = self._extensions_dir / f"{info['name']}.py"
        if script_path.exists():
            try:
                with open(script_path, 'r', encoding='utf-8') as f:
                    code_content = f.read()
                    # 截断过长代码（可调整）
                    if len(code_content) > 3000:
                        code_content = code_content[:3000] + "\n... (代码过长，已截断)"
                    lines.append(f"\n代码内容:\n```python\n{code_content}\n```")
            except Exception as e:
                lines.append(f"\n读取代码失败: {e}")
        else:
            lines.append(f"\n代码文件不存在: {script_path}")
        # ===== 新增结束 =====

        
        if info.get('usage_guide'):
            lines.append(f"\n使用说明:\n{info['usage_guide']}")
        
        if info.get('version_history'):
            lines.append(f"\n版本历史 (最近5个):")
            for v in info['version_history'][:5]:
                lines.append(f"  [{v['id']}] v{v['version']} - {v['created_at'][:19]}")
                if v.get('changelog'):
                    lines.append(f"      {v['changelog']}")
        
        return "\n".join(lines)
    
    def _handle_search(self, payload: Dict) -> str:
        """搜索扩展"""
        query = payload.get("query", "")
        if not query:
            return "请指定搜索关键词 (query)"
        
        results = self.db.search_extensions(query)
        if not results:
            return f"未找到与 '{query}' 相关的扩展"
        
        lines = [f"找到 {len(results)} 个扩展:", ""]
        for ext in results[:20]:
            lines.append(f"  [{ext['id']}] {ext['name']} v{ext['version']}")
            lines.append(f"      {ext['description'][:100]}")
            if ext.get('usage_guide'):
                lines.append(f"      使用: {ext['usage_guide'][:80]}...")
            lines.append(f"      导入: {ext['import_count']}次")
            lines.append("")
        
        return "\n".join(lines)
    
    def _handle_add(self, payload: Dict) -> str:
        """添加扩展"""
        name = payload.get("extension_name", "").strip()
        description = payload.get("description", "").strip()
        code = payload.get("code", "").strip()
        entry_point = payload.get("entry_point", "run")
        author = payload.get("author", "")
        version = payload.get("version", "1.0.0")
        dependencies = payload.get("dependencies", [])
        usage_guide = payload.get("usage_guide", "")
        timeout = payload.get("timeout", 30)
        
        if not name:
            return "缺少必填字段: name"
        if not description:
            return "缺少必填字段: description"
        if not code:
            return "缺少必填字段: code"
        
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', name):
            return f"名称格式无效: {name} (只能包含字母、数字、下划线，且不能以数字开头)"
        
        existing = self.db.get_extension_by_name(name)
        
        # 只有 active 才算存在，disabled 允许覆盖
        if existing and existing['status'] == 'active':
            return f"扩展已存在: {name}"
        
        if f'def {entry_point}' not in code:
            return f"代码中未找到入口函数: {entry_point}，请确保包含 def {entry_point}(**kwargs):"
        
        ext_id = self.create_extension(
            name=name,
            description=description,
            code=code,
            entry_point=entry_point,
            author=author,
            version=version,
            dependencies=dependencies,
            usage_guide=usage_guide,
            timeout=timeout
        )
        
        if ext_id:
            return f"扩展创建成功! ID: {ext_id}, 名称: {name}, 版本: {version}"
        else:
            return "扩展创建失败，请检查代码格式或日志"
    
    def _handle_update(self, payload: Dict) -> str:
        """更新扩展代码"""
        ext_id = payload.get("extension_id")
        ext_name = payload.get("extension_name")
        code = payload.get("code", "").strip()
        changelog = payload.get("changelog", "")
        
        if not code:
            return "缺少必填字段: code"
        
        if ext_name and not ext_id:
            ext = self.db.get_extension_by_name(ext_name)
            if ext:
                ext_id = ext['id']
        
        if not ext_id:
            return f"未找到扩展: {ext_name or '未指定'}"
        
        ext = self.db.get_extension(ext_id)
        if not ext:
            return f"扩展 ID {ext_id} 不存在"
        
        entry_point = ext.get('entry_point', 'run')
        if f'def {entry_point}' not in code:
            return f"代码中未找到入口函数: {entry_point}，请确保包含 def {entry_point}(**kwargs):"
        
        if self.update_extension_code(ext_id, code, changelog):
            return f"扩展 '{ext['name']}' 已更新 (版本: {ext['version']} -> 新版本)"
        else:
            return "扩展更新失败"
    
    def _handle_update_info(self, payload: Dict) -> str:
        """更新扩展信息"""
        ext_id = payload.get("extension_id")
        ext_name = payload.get("extension_name")
        
        if ext_name and not ext_id:
            ext = self.db.get_extension_by_name(ext_name)
            if ext:
                ext_id = ext['id']
        
        if not ext_id:
            return f"未找到扩展: {ext_name or '未指定'}"
        
        ext = self.db.get_extension(ext_id)
        if not ext:
            return f"扩展 ID {ext_id} 不存在"
        
        update_fields = {}
        allowed_fields = ['description', 'version', 'status', 'usage_guide', 
                         'author', 'timeout', 'entry_point']
        
        for field in allowed_fields:
            if field in payload:
                value = payload[field]
                if field == 'timeout':
                    try:
                        value = int(value)
                    except:
                        return f"timeout 必须是数字: {value}"
                update_fields[field] = value
        
        if not update_fields:
            return "没有需要更新的字段"
        
        if self.db.update_extension(ext_id, **update_fields):
            # 清除缓存
            cache_key = f"{ext_id}_{ext['version']}"
            if cache_key in self._loaded_extensions:
                del self._loaded_extensions[cache_key]
            
            return f"扩展 '{ext['name']}' 信息已更新: {', '.join(update_fields.keys())}"
        else:
            return "扩展信息更新失败"
    
    def _handle_delete(self, payload: Dict) -> str:
        """删除扩展"""
        ext_id = payload.get("extension_id")
        ext_name = payload.get("extension_name")
        permanent = payload.get("permanent", False)
        
        if ext_name and not ext_id:
            ext = self.db.get_extension_by_name(ext_name)
            if ext:
                ext_id = ext['id']
        
        if not ext_id:
            return f"未找到扩展: {ext_name or '未指定'}"
        
        ext = self.db.get_extension(ext_id)
        if not ext:
            return f"扩展 ID {ext_id} 不存在"
        
        if self.delete_extension(ext_id, permanent=permanent):
            action = "永久删除" if permanent else "软删除"
            return f"扩展 '{ext['name']}' 已{action}"
        else:
            return "扩展删除失败"
    
    def _handle_rollback(self, payload: Dict) -> str:
        """回滚扩展"""
        ext_id = payload.get("extension_id")
        ext_name = payload.get("extension_name")
        version_id = payload.get("version_id")
        
        if not version_id:
            return "缺少必填字段: version_id"
        
        if ext_name and not ext_id:
            ext = self.db.get_extension_by_name(ext_name)
            if ext:
                ext_id = ext['id']
        
        if not ext_id:
            return f"未找到扩展: {ext_name or '未指定'}"
        
        ext = self.db.get_extension(ext_id)
        if not ext:
            return f"扩展 ID {ext_id} 不存在"
        
        version_info = self.db.get_version_by_id(version_id)
        if not version_info or version_info['extension_id'] != ext_id:
            return f"版本 ID {version_id} 不存在或不属于此扩展"
        
        if self.rollback_extension(ext_id, version_id):
            return f"扩展 '{ext['name']}' 已回滚到版本 v{version_info['version']}"
        else:
            return "扩展回滚失败"
    
    def _handle_history(self, payload: Dict) -> str:
        """获取版本历史"""
        ext_id = payload.get("extension_id")
        ext_name = payload.get("extension_name")
        limit = payload.get("limit", 20)
        
        if ext_name and not ext_id:
            ext = self.db.get_extension_by_name(ext_name)
            if ext:
                ext_id = ext['id']
        
        if not ext_id:
            return f"未找到扩展: {ext_name or '未指定'}"
        
        ext = self.db.get_extension(ext_id)
        if not ext:
            return f"扩展 ID {ext_id} 不存在"
        
        versions = self.db.get_version_history(ext_id, limit=limit)
        
        if not versions:
            return f"扩展 '{ext['name']}' 暂无版本历史"
        
        lines = [
            f"扩展 '{ext['name']}' 版本历史 (共 {len(versions)} 个):",
            "",
            f"{'ID':<6} {'版本':<12} {'创建时间':<20} {'变更说明'}"
        ]
        lines.append("-" * 80)
        
        for v in versions:
            changelog = v.get('changelog', '')[:30] if v.get('changelog') else ''
            lines.append(
                f"{v['id']:<6} v{v['version']:<11} {v['created_at'][:19]:<20} {changelog}"
            )
        
        return "\n".join(lines)
    
    def _handle_call(self, payload: Dict) -> str:
        """调用扩展"""
        ext_id = payload.get("extension_id")
        ext_name = payload.get("extension_name")
        params = payload.get("params", {})
        
        if ext_name and not ext_id:
            ext = self.db.get_extension_by_name(ext_name)
            if ext:
                ext_id = ext['id']
        
        if not ext_id:
            return f"未找到扩展: {ext_name or '未指定'}"
        
        ext = self.db.get_extension(ext_id)
        if not ext:
            return f"扩展 ID {ext_id} 不存在"
        
        if ext['status'] != 'active':
            return f"扩展 '{ext['name']}' 已禁用"
        
        result = self.call_extension(ext_id, params)
        
        if result['status'] == 'success':
            result_data = result.get('result', {})
            if isinstance(result_data, dict):
                if 'message' in result_data:
                    return f"[{ext['name']}] {result_data['message']}\n{json.dumps(result_data.get('data', {}), ensure_ascii=False, indent=2)}"
                elif 'data' in result_data:
                    return f"[{ext['name']}] 执行成功\n{json.dumps(result_data['data'], ensure_ascii=False, indent=2)}"
                else:
                    return f"[{ext['name']}] 执行成功\n{json.dumps(result_data, ensure_ascii=False, indent=2)}"
            else:
                return f"[{ext['name']}] {result_data}"
        elif result['status'] == 'timeout':
            return f"[{ext['name']}] 执行超时（>{ext.get('timeout', 30)}秒）"
        else:
            return f"[{ext['name']}] 执行失败: {result.get('error', '未知错误')}"
    
    # ========== 核心功能实现 ==========
    
    def create_extension(self, name: str, description: str, code: str,
                         entry_point: str = 'run', author: str = '',
                         version: str = '1.0.0', dependencies: List[str] = None,
                         usage_guide: str = '', timeout: int = 30) -> Optional[int]:
        
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', name):
            self.log(f"扩展名称格式无效: {name}", "ERROR")
            return None

        existing = self.db.get_extension_by_name(name)
        
        # 如果存在 disabled 记录，清理掉
        if existing and existing['status'] == 'disabled':
            self.db.permanently_delete(existing['id'])
            self.log(f"已清理软删除记录: {name}")
        elif existing and existing['status'] == 'active':
            self.log(f"扩展已存在: {name}", "ERROR")
            return None

        # 固定路径：./extensions/{name}.py
        script_path = self._extensions_dir / f"{name}.py"

        # 如果文件已存在（比如之前被软删除），先备份
        if script_path.exists():
            backup_path = self._versions_dir / f"{name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.py"
            shutil.move(str(script_path), str(backup_path))
            self.log(f"已备份旧文件: {backup_path.name}")
        
        try:
            with open(script_path, 'w', encoding='utf-8') as f:
                f.write(code)
        except Exception as e:
            self.log(f"保存脚本失败: {e}", "ERROR")
            return None
        
        try:
            ext_id = self.db.add_extension(
                name=name,
                description=description,
                script_path=f"{name}.py",
                entry_point=entry_point,
                author=author,
                version=version,
                dependencies=dependencies or [],
                usage_guide=usage_guide,
                timeout=timeout
            )
            self.log(f"扩展创建成功: {name} (ID: {ext_id})")
            return ext_id
        except Exception as e:
            self.log(f"保存到数据库失败: {e}", "ERROR")
            if script_path.exists():
                script_path.unlink()
            return None
    
    def load_extension(self, extension_id: int, force_reload: bool = False) -> Optional[Dict]:
        """加载扩展能力"""
        ext = self.db.get_extension(extension_id)
        if not ext:
            self.log(f"扩展不存在: ID={extension_id}", "ERROR")
            return None
        
        if ext['status'] != 'active':
            self.log(f"扩展已禁用: {ext['name']}", "WARN")
            return None
        
        cache_key = f"{extension_id}_{ext['version']}"
        if not force_reload and cache_key in self._loaded_extensions:
            return self._loaded_extensions[cache_key]
        
        safe, msg, abs_path = self.path_validator.validate_path(ext['script_path'])
        if not safe:
            self.log(f"路径验证失败: {msg}", "ERROR")
            return None
        
        deps = json.loads(ext.get('dependencies', '[]'))
        if deps:
            ok, missing = DependencyChecker.check_dependencies(deps)
            if not ok:
                self.log(f"依赖检查失败: {missing}", "WARN")
                ok, installed = DependencyChecker.auto_install_dependencies(deps)
                if ok:
                    self.log(f"自动安装依赖成功: {installed}")
                else:
                    self.log(f"自动安装依赖失败: {missing}", "ERROR")
                    return None
        
        try:
            with self.import_guard.guard_import(ext['name']):
                module = self._safe_import_module(ext['name'], abs_path)
                if not module:
                    return None
                
                entry_point = ext.get('entry_point', 'run')
                func = getattr(module, entry_point, None)
                if not callable(func):
                    self.log(f"找不到入口函数: {entry_point}", "ERROR")
                    return None
                
                self.db.update_extension_stats(extension_id)
                
                result = {
                    'id': extension_id,
                    'name': ext['name'],
                    'func': func,
                    'module': module,
                    'info': ext,
                    'version': ext['version']
                }
                self._loaded_extensions[cache_key] = result
                
                self.log(f"扩展加载成功: {ext['name']} v{ext['version']}")
                return result
                
        except ImportError as e:
            self.log(f"导入错误: {e}", "ERROR")
            return None
        except Exception as e:
            self.log(f"加载失败: {e}", "ERROR")
            return None
    
    def _safe_import_module(self, name: str, path: Path):
        """安全导入模块"""
        try:
            module_name = f"extension_{name}_{hashlib.md5(str(path).encode()).hexdigest()[:8]}"
            
            if module_name in self._module_cache:
                return self._module_cache[module_name]
            
            safe_import = self.import_guard.create_safe_import_hook()
            
            spec = importlib.util.spec_from_file_location(module_name, path)
            if not spec or not spec.loader:
                return None
            
            module = importlib.util.module_from_spec(spec)
            
            if hasattr(module, '__builtins__'):
                if isinstance(module.__builtins__, dict):
                    module.__builtins__['__import__'] = safe_import
                else:
                    safe_builtins = {
                        '__import__': safe_import,
                        'print': print,
                        'len': len,
                        'str': str,
                        'int': int,
                        'float': float,
                        'list': list,
                        'dict': dict,
                        'tuple': tuple,
                        'bool': bool,
                        'set': set,
                        'range': range,
                        'enumerate': enumerate,
                        'zip': zip,
                        'sum': sum,
                        'min': min,
                        'max': max,
                        'abs': abs,
                        'round': round,
                        'chr': chr,
                        'ord': ord,
                        'hex': hex,
                        'oct': oct,
                        'bin': bin,
                        'sorted': sorted,
                        'reversed': reversed,
                        'map': map,
                        'filter': filter,
                        'any': any,
                        'all': all,
                        'isinstance': isinstance,
                        'issubclass': issubclass,
                        'type': type,
                        'object': object,
                        'property': property,
                        'staticmethod': staticmethod,
                        'classmethod': classmethod,
                    }
                    module.__builtins__ = safe_builtins
            
            spec.loader.exec_module(module)
            self._module_cache[module_name] = module
            return module
            
        except Exception as e:
            self.log(f"模块加载失败: {e}", "ERROR")
            return None
    
    def call_extension(self, extension_id: int, params: Dict = None) -> Dict:
        """调用扩展能力"""
        params = params or {}
        
        ext_info = self.load_extension(extension_id)
        if not ext_info:
            return {
                'status': 'error',
                'error': '扩展加载失败'
            }
        
        ext = self.db.get_extension(extension_id)
        timeout = ext.get('timeout', 30)
        
        result = self.isolation.execute_with_isolation(
            ext_info['func'],
            params,
            timeout
        )
        
        if result['status'] == 'success':
            self.log(f"扩展执行成功: {ext['name']}")
        else:
            self.log(f"扩展执行失败: {ext['name']} - {result.get('error', '未知错误')}", "ERROR")
        
        return result
    
    def delete_extension(self, extension_id: int, permanent: bool = False) -> bool:
        """删除扩展"""
        ext = self.db.get_extension(extension_id)
        if not ext:
            return False
        
        try:
            script_path = self._extensions_dir / ext['script_path']
            if script_path.exists():
                backup_path = self._versions_dir / f"{ext['name']}_deleted_{datetime.now().strftime('%Y%m%d_%H%M%S')}.py"
                shutil.copy2(script_path, backup_path)
                script_path.unlink()
        except Exception as e:
            self.log(f"清理脚本文件失败: {e}", "WARN")
        
        to_remove = []
        for key in self._loaded_extensions:
            if self._loaded_extensions[key].get('id') == extension_id:
                to_remove.append(key)
        for key in to_remove:
            del self._loaded_extensions[key]
        
        if permanent:
            self.db.permanently_delete(extension_id)
        else:
            self.db.delete_extension(extension_id)
        
        self.log(f"扩展已删除: {ext['name']}")
        return True
    
    def update_extension_code(self, extension_id: int, new_code: str, 
                               changelog: str = "") -> bool:
        """更新扩展代码（升级）"""
        ext = self.db.get_extension(extension_id)
        if not ext:
            return False
        
        old_path = self._extensions_dir / ext['script_path']
        if old_path.exists():
            backup_name = f"{ext['name']}_v{ext['version']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.py"
            backup_path = self._versions_dir / backup_name
            shutil.copy2(old_path, backup_path)
            
            self.db.add_version_history(
                extension_id,
                ext['version'],
                backup_path.name,
                f"升级前备份: {changelog}"
            )
        
        try:
            with open(old_path, 'w', encoding='utf-8') as f:
                f.write(new_code)
        except Exception as e:
            self.log(f"写入新代码失败: {e}", "ERROR")
            return False
        
        old_version = ext['version']
        version_parts = old_version.split('.')
        if len(version_parts) == 3:
            version_parts[2] = str(int(version_parts[2]) + 1)
            new_version = '.'.join(version_parts)
        else:
            new_version = '1.0.1'
        
        self.db.update_extension(extension_id, version=new_version)
        
        cache_key = f"{extension_id}_{ext['version']}"
        if cache_key in self._loaded_extensions:
            del self._loaded_extensions[cache_key]
        
        self.log(f"扩展已升级: {ext['name']} {old_version} -> {new_version}")
        return True
    
    def rollback_extension(self, extension_id: int, version_id: int) -> bool:
        """回滚到指定版本"""
        ext = self.db.get_extension(extension_id)
        if not ext:
            return False
        
        version_info = self.db.get_version_by_id(version_id)
        if not version_info or version_info['extension_id'] != extension_id:
            return False
        
        current_path = Path(ext['script_path'])
        if current_path.exists():
            backup_name = f"{ext['name']}_rollback_{datetime.now().strftime('%Y%m%d_%H%M%S')}.py"
            backup_path = self._versions_dir / backup_name
            shutil.copy2(current_path, backup_path)
        
        target_path = Path(version_info['script_path'])
        if not target_path.exists():
            return False
        
        shutil.copy2(target_path, current_path)
        
        self.db.update_extension(extension_id, version=version_info['version'])
        
        cache_key = f"{extension_id}_{ext['version']}"
        if cache_key in self._loaded_extensions:
            del self._loaded_extensions[cache_key]
        
        self.log(f"扩展已回滚: {ext['name']} -> v{version_info['version']}")
        return True
    
    def get_extension_info(self, extension_id: int) -> Dict:
        """获取扩展详细信息"""
        ext = self.db.get_extension(extension_id)
        if not ext:
            return {'error': '扩展不存在'}
        
        versions = self.db.get_version_history(extension_id)
        script_exists = (self._extensions_dir / ext['script_path']).exists()
        
        return {
            'id': ext['id'],
            'name': ext['name'],
            'description': ext['description'],
            'version': ext['version'],
            'status': ext['status'],
            'import_count': ext['import_count'],
            'last_used_at': ext['last_used_at'],
            'created_at': ext['created_at'],
            'updated_at': ext['updated_at'],
            'script_exists': script_exists,
            'script_path': ext['script_path'],
            'entry_point': ext['entry_point'],
            'author': ext.get('author', ''),
            'dependencies': json.loads(ext.get('dependencies', '[]')),
            'timeout': ext.get('timeout', 30),
            'usage_guide': ext.get('usage_guide', ''),
            'version_history': versions[:5],
            'is_loaded': any(
                self._loaded_extensions.get(k, {}).get('id') == extension_id 
                for k in self._loaded_extensions
            )
        }
    
    def shutdown(self):
        """清理资源"""
        self.isolation.shutdown()
        self._module_cache.clear()
        self._loaded_extensions.clear()


# ============================================================================
# 扩展模板生成器
# ============================================================================

def generate_extension_template(name: str, description: str, author: str = "") -> str:
    """生成扩展脚本模板"""
    return f'''#!/usr/bin/env python3
"""
扩展能力: {name}
描述: {description}
作者: {author}
创建时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
"""

import json
from typing import Dict, Any


def run(**kwargs) -> Dict[str, Any]:
    """
    入口函数
    
    Args:
        **kwargs: 调用时传递的参数
    
    Returns:
        Dict: 执行结果
            {{
                "status": "success" | "error",
                "data": 返回的数据,
                "message": "执行信息"
            }}
    """
    try:
        # ===== 获取参数 =====
        # 示例: city = kwargs.get('city', '')
        # if not city:
        #     return {{"status": "error", "message": "缺少必要参数: city"}}
        
        # ===== 执行逻辑 =====
        # 在这里编写你的代码
        
        # ===== 返回结果 =====
        return {{
            "status": "success",
            "data": {{
                "result": "执行成功",
                "params": kwargs
            }},
            "message": "扩展执行完成"
        }}
        
    except Exception as e:
        return {{
            "status": "error",
            "message": f"执行异常: {{str(e)}}",
            "error_type": type(e).__name__
        }}


if __name__ == "__main__":
    print(f"扩展能力: {name}")
    print(f"描述: {description}")
    print("请通过 ExtensionManager 调用此扩展")
'''
