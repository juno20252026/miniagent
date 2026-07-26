#!/usr/bin/env python3
"""
Mission Manager - 独立的任务管理模块

功能：
1. 将 MISSION 从经验表（lessons）迁移到独立的 JSON 文件存储
2. 任务列表写入 MISSION_DIR 目录下的 JSON 文件
3. 任务详情由 AI 调用脚本生成 JSON
4. 提供任务列表的 CRUD 操作
5. 心跳时注入未完成的任务列表
6. 定时执行任务功能
7. 代码任务：定时执行预先写入的代码，结果返回给AI
"""

import json
import os
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, asdict, field
from enum import Enum


# ============================================================================
# 配置
# ============================================================================

MISSION_DIR = Path("./MISSION")
MISSION_DIR.mkdir(parents=True, exist_ok=True)

TASKS_FILE = MISSION_DIR / "tasks.json"

# 定时任务检查间隔（秒）
SCHEDULE_CHECK_INTERVAL = 30
# 最大并发任务数
MAX_CONCURRENT_TASKS = 1

# ===== 代码任务执行限制 =====
CODE_TASK_EXECUTION_LIMIT = 50   # 每个任务最大执行次数（硬限制）
CODE_TASK_GLOBAL_LIMIT = 100     # 全局最大执行次数
CODE_TASK_MIN_INTERVAL = 5       # 代码任务最小执行间隔（秒）


# ============================================================================
# 任务状态枚举
# ============================================================================

class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SCHEDULED = "scheduled"


class TaskPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class TaskType(str, Enum):
    AI = "ai"
    CODE = "code"


# ============================================================================
# 数据类
# ============================================================================

@dataclass
class MissionTask:
    """任务数据结构"""
    id: str
    name: str
    description: str = ""
    status: str = TaskStatus.PENDING
    priority: str = TaskPriority.NORMAL
    progress: float = 0.0
    created_at: str = ""
    updated_at: str = ""
    started_at: str = ""
    completed_at: str = ""
    scheduled_at: str = ""
    repeat_interval: int = 0
    repeat_count: int = 0
    max_repeats: int = 0
    parent_id: str = ""
    tags: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    result: str = ""
    error: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    # ===== 任务类型和代码执行字段 =====
    task_type: str = TaskType.AI
    code_to_execute: str = ""
    execution_result: str = ""
    last_executed_at: str = ""
    execute_on_schedule: bool = False
    auto_execute_count: int = 0
    max_auto_execute: int = 0

    def to_dict(self) -> Dict:
        data = asdict(self)
        data['status'] = self.status.value if hasattr(self.status, 'value') else self.status
        data['priority'] = self.priority.value if hasattr(self.priority, 'value') else self.priority
        data['task_type'] = self.task_type.value if hasattr(self.task_type, 'value') else self.task_type
        return data

    @classmethod
    def from_dict(cls, data: Dict) -> 'MissionTask':
        if 'status' in data and isinstance(data['status'], str):
            try:
                data['status'] = TaskStatus(data['status'])
            except ValueError:
                data['status'] = TaskStatus.PENDING
        if 'priority' in data and isinstance(data['priority'], str):
            try:
                data['priority'] = TaskPriority(data['priority'])
            except ValueError:
                data['priority'] = TaskPriority.NORMAL
        if 'task_type' in data and isinstance(data['task_type'], str):
            try:
                data['task_type'] = TaskType(data['task_type'])
            except ValueError:
                data['task_type'] = TaskType.AI
        return cls(**data)


# ============================================================================
# Mission Manager 主类
# ============================================================================

class MissionManager:
    """
    任务管理器
    """

    def __init__(self, 
                 mission_dir: Path = None,
                 trigger_heartbeat: Callable = None,
                 logger: Callable = None,
                 sandbox: Any = None):
        """
        初始化任务管理器
        
        Args:
            mission_dir: 任务存储目录
            trigger_heartbeat: 心跳触发器
            logger: 日志回调
            sandbox: 沙盒实例（用于执行代码任务）
        """
        self.mission_dir = mission_dir or MISSION_DIR
        self.trigger_heartbeat = trigger_heartbeat
        self.logger = logger or (lambda x: None)
        self.sandbox = sandbox
        
        self._tasks: Dict[str, MissionTask] = {}
        self._running_tasks: Dict[str, threading.Thread] = {}
        self._lock = threading.RLock()
        self._running = False
        self._scheduler_thread: Optional[threading.Thread] = None
        self._ai_callback: Optional[Callable[[str], None]] = None
        
        # ===== 代码任务执行计数器 =====
        self._global_code_exec_count = 0
        self._code_exec_lock = threading.Lock()
        self._execution_history: Dict[str, List[str]] = {}
        self._history_lock = threading.Lock()
        
        self._load_tasks()
        self._cleanup_stale_tasks()

    # ========== 日志 ==========

    def _log(self, msg: str, level: str = "info"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.logger(f"[{timestamp}] [MissionManager] {msg}")

    # ========== 持久化 ==========

    def _load_tasks(self):
        with self._lock:
            if not TASKS_FILE.exists():
                self._tasks = {}
                return
            try:
                with open(TASKS_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self._tasks = {}
                for item in data.get('tasks', []):
                    task = MissionTask.from_dict(item)
                    self._tasks[task.id] = task
                self._log(f"加载了 {len(self._tasks)} 个任务")
            except Exception as e:
                self._log(f"加载任务列表失败: {e}", "error")
                self._tasks = {}

    def _save_tasks(self):
        with self._lock:
            try:
                data = {
                    'version': '1.0',
                    'updated_at': datetime.now().isoformat(),
                    'total_tasks': len(self._tasks),
                    'tasks': [task.to_dict() for task in self._tasks.values()]
                }
                with open(TASKS_FILE, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception as e:
                self._log(f"保存任务列表失败: {e}", "error")

    def _generate_unique_id(self) -> str:
        max_attempts = 10
        for _ in range(max_attempts):
            task_id = str(uuid.uuid4())[:8]
            if task_id not in self._tasks:
                return task_id
        return str(uuid.uuid4())

    # ========== 任务 CRUD（统一接口） ==========

    def create_task(self, 
                    name: str,
                    description: str = "",
                    priority: str = TaskPriority.NORMAL,
                    scheduled_at: str = "",
                    repeat_interval: int = 0,
                    max_repeats: int = 0,
                    tags: List[str] = None,
                    dependencies: List[str] = None,
                    metadata: Dict[str, Any] = None,
                    # ===== 代码任务参数 =====
                    task_type: str = TaskType.AI,
                    code: str = "",
                    max_auto_execute: int = 0) -> MissionTask:
        """
        创建任务（统一接口，支持 AI 任务和代码任务）
        
        Args:
            name: 任务名称
            description: 任务描述
            priority: 优先级
            scheduled_at: 计划执行时间
            repeat_interval: 重复间隔（秒）
            max_repeats: 最大重复次数
            tags: 标签列表
            dependencies: 依赖的任务ID列表
            metadata: 扩展元数据
            task_type: "ai" 或 "code"
            code: 代码任务要执行的 Python 代码
            max_auto_execute: 代码任务最大自动执行次数
        """
        with self._lock:
            task_id = self._generate_unique_id()
            
            # 处理 task_type
            if isinstance(task_type, str):
                try:
                    task_type = TaskType(task_type)
                except ValueError:
                    task_type = TaskType.AI
            
            task = MissionTask(
                id=task_id,
                name=name[:100],
                description=description[:500],
                priority=TaskPriority(priority) if isinstance(priority, str) else priority,
                created_at=datetime.now().isoformat(),
                updated_at=datetime.now().isoformat(),
                scheduled_at=scheduled_at,
                repeat_interval=repeat_interval,
                max_repeats=max_repeats,
                tags=tags or [],
                dependencies=dependencies or [],
                metadata=metadata or {},
                progress=0.0,
                status=TaskStatus.SCHEDULED if scheduled_at else TaskStatus.PENDING,
                # ===== 代码任务字段 =====
                task_type=task_type,
                code_to_execute=code,
                max_auto_execute=max_auto_execute,
                execute_on_schedule=bool(scheduled_at or repeat_interval)
            )

            self._tasks[task.id] = task
            self._save_tasks()
            self._log(f"创建任务: {task.id} - {task.name} (类型: {task_type.value})")
            return task

    def get_task(self, task_id: str) -> Optional[MissionTask]:
        with self._lock:
            return self._tasks.get(task_id)

    def get_task_detail(self, task_id: str) -> Optional[Dict]:
        task = self.get_task(task_id)
        if not task:
            return None
        return task.to_dict()

    def update_task(self, task_id: str, auto_reschedule: bool = True, **kwargs) -> Optional[MissionTask]:
        """
        更新任务
        
        Args:
            task_id: 任务ID
            auto_reschedule: 是否自动重新调度循环任务
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None

            for key, value in kwargs.items():
                if hasattr(task, key) and key not in ['id', 'created_at']:
                    if key == 'status' and isinstance(value, str):
                        try:
                            value = TaskStatus(value)
                        except ValueError:
                            pass
                    elif key == 'priority' and isinstance(value, str):
                        try:
                            value = TaskPriority(value)
                        except ValueError:
                            pass
                    elif key == 'task_type' and isinstance(value, str):
                        try:
                            value = TaskType(value)
                        except ValueError:
                            pass
                    setattr(task, key, value)

            task.updated_at = datetime.now().isoformat()
            
            if task.status == TaskStatus.COMPLETED and not task.completed_at:
                task.completed_at = datetime.now().isoformat()

            self._save_tasks()
            
            # 循环任务重新调度
            if task.status == TaskStatus.COMPLETED and task.repeat_interval > 0 and auto_reschedule:
                if task.max_repeats > 0 and task.repeat_count >= task.max_repeats:
                    self._log(f"循环任务 {task_id} 达到最大次数 {task.max_repeats}，已终止")
                    if task.id in self._tasks:
                        del self._tasks[task.id]
                        self._save_tasks()
                else:
                    task.status = TaskStatus.SCHEDULED
                    task.repeat_count += 1
                    task.completed_at = ""
                    task.scheduled_at = (datetime.now() + timedelta(seconds=task.repeat_interval)).isoformat()
                    task.updated_at = datetime.now().isoformat()
                    self._save_tasks()
                    self._log(f"循环任务 {task_id} 重新调度 (第 {task.repeat_count} 次)")
                    return task
            
            # 任务结束（完成/失败/取消）时从列表中移除
            if task.status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]:
                if task.id in self._tasks:
                    del self._tasks[task.id]
                    self._save_tasks()
            
            self._log(f"更新任务: {task_id} - {task.name} -> {task.status}")
            return task

    def delete_task(self, task_id: str, archive: bool = True) -> bool:
        """
        删除任务
        
        Args:
            task_id: 任务ID
            archive: 是否归档（True: 移到归档目录，False: 永久删除）- 保留参数但不再区分
        """
        with self._lock:
            if task_id not in self._tasks:
                return False
            
            task = self._tasks[task_id]
            
            if task_id in self._running_tasks:
                self._log(f"任务 {task_id} 正在运行，先取消", "warning")
                self.cancel_task(task_id)
            
            # 直接从内存中删除
            del self._tasks[task_id]
            self._save_tasks()
            self._log(f"删除任务: {task_id}")
            return True

    def cancel_task(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False
            if task.status in [TaskStatus.COMPLETED, TaskStatus.CANCELLED]:
                return False
            task.status = TaskStatus.CANCELLED
            task.updated_at = datetime.now().isoformat()
            # 取消后从列表中移除
            if task.id in self._tasks:
                del self._tasks[task.id]
                self._save_tasks()
            self._log(f"取消任务: {task_id}")
            return True

    def _remove_task_from_list(self, task: MissionTask):
        """从任务列表中移除任务（内部方法）"""
        if task.id in self._tasks:
            del self._tasks[task.id]
            self._save_tasks()

    # ========== 获取 Agent（安全） ==========

    def _get_agent_from_trigger(self):
        if not self.trigger_heartbeat:
            return None
        if hasattr(self.trigger_heartbeat, '__self__'):
            return self.trigger_heartbeat.__self__
        if hasattr(self.trigger_heartbeat, 'im_self'):
            return self.trigger_heartbeat.im_self
        if hasattr(self.trigger_heartbeat, 'self'):
            return self.trigger_heartbeat.self
        return None

    def _trigger_heartbeat_for_task(self, task_id: str = None):
        if not self.trigger_heartbeat:
            return
        agent = self._get_agent_from_trigger()
        if agent:
            if task_id and hasattr(agent, '_current_mission_task_id'):
                agent._current_mission_task_id = task_id
            if hasattr(agent, '_introspection_source'):
                agent._introspection_source = "mission"
            if hasattr(agent, 'prompt_assembler'):
                try:
                    agent.prompt_assembler.load_modules(["task_manage"], "任务管理器触发")
                except Exception:
                    pass
        threading.Thread(target=self.trigger_heartbeat, daemon=True).start()

    # ========== 任务查询 ==========

    def get_tasks(self, 
                  status: Optional[str] = None,
                  priority: Optional[str] = None,
                  tag: Optional[str] = None,
                  task_type: Optional[str] = None,
                  limit: int = 100) -> List[MissionTask]:
        with self._lock:
            tasks = list(self._tasks.values())
            if status:
                tasks = [t for t in tasks if t.status == TaskStatus(status)]
            if priority:
                tasks = [t for t in tasks if t.priority == TaskPriority(priority)]
            if tag:
                tasks = [t for t in tasks if tag in t.tags]
            if task_type:
                tasks = [t for t in tasks if t.task_type == TaskType(task_type)]
            
            priority_order = {
                TaskPriority.CRITICAL: 0,
                TaskPriority.HIGH: 1,
                TaskPriority.NORMAL: 2,
                TaskPriority.LOW: 3
            }
            tasks.sort(key=lambda t: priority_order.get(t.priority, 2))
            return tasks[:limit]

    def get_pending_tasks(self) -> List[MissionTask]:
        return self.get_tasks(status=TaskStatus.PENDING)

    def get_scheduled_tasks(self) -> List[MissionTask]:
        return self.get_tasks(status=TaskStatus.SCHEDULED)

    def get_running_tasks(self) -> List[MissionTask]:
        return self.get_tasks(status=TaskStatus.RUNNING)

    def get_tasks_for_prompt(self, limit: int = 10) -> str:
        with self._lock:
            pending = self.get_pending_tasks()[:limit]
            scheduled = self.get_scheduled_tasks()[:limit // 2]
            running = self.get_running_tasks()[:limit // 2]

            if not pending and not scheduled and not running:
                return ""

            lines = ["", "## 当前任务列表", ""]

            if running:
                lines.append("### 执行中任务:")
                for t in running:
                    lines.append(f"  - [{t.id}] {t.name} (进度: {t.progress*100:.0f}%)")
                lines.append("")

            if scheduled:
                lines.append("### 已调度任务:")
                for t in scheduled:
                    scheduled_time = t.scheduled_at[:16] if t.scheduled_at else "待定"
                    lines.append(f"  - [{t.id}] {t.name} (计划: {scheduled_time})")
                lines.append("")

            if pending:
                lines.append("### 待执行任务:")
                for t in pending:
                    repeat_info = f" (循环:{t.repeat_interval}s)" if t.repeat_interval > 0 else ""
                    type_info = f" [代码]" if t.task_type == TaskType.CODE else ""
                    lines.append(f"  - [{t.id}]{type_info} {t.name}{repeat_info}: {t.description}")
                lines.append("")

            return "\n".join(lines)

    # ========== 任务执行 ==========

    def execute_task(self, task_id: str, async_mode: bool = True) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                self._log(f"任务不存在: {task_id}", "error")
                return False

            if task.status in [TaskStatus.RUNNING, TaskStatus.COMPLETED]:
                self._log(f"任务无法执行: {task_id} 状态为 {task.status}", "warning")
                return False

            for dep_id in task.dependencies:
                dep = self._tasks.get(dep_id)
                if dep and dep.status != TaskStatus.COMPLETED:
                    self._log(f"任务 {task_id} 依赖 {dep_id} 未完成", "warning")
                    return False

            task.status = TaskStatus.RUNNING
            task.started_at = datetime.now().isoformat()
            task.updated_at = datetime.now().isoformat()
            self._save_tasks()

        if async_mode:
            thread = threading.Thread(target=self._run_task, args=(task_id,), daemon=True)
            with self._lock:
                self._running_tasks[task_id] = thread
            thread.start()
            self._log(f"异步启动任务: {task_id}")
        else:
            self._run_task(task_id)

        return True

    def _run_task(self, task_id: str):
        try:
            task = self.get_task(task_id)
            if not task:
                self._log(f"任务在执行前被删除: {task_id}", "error")
                return

            self._log(f"开始执行任务: {task.name} (ID: {task_id})")
            
            if task.task_type == TaskType.CODE:
                self._execute_code_task(task_id)
                return
            
            # AI 任务：触发心跳
            if self.trigger_heartbeat:
                self.update_task(task_id, status=TaskStatus.PENDING)
                self._trigger_heartbeat_for_task(task.id)
                self._log(f"已触发心跳执行任务: {task_id}")

                agent = self._get_agent_from_trigger()
                if agent and hasattr(agent, 'act_log'):
                    agent.act_log.append(f"[{datetime.now().strftime('%H:%M:%S')}]开始执行任务: [{task.id}] {task.name}")
                
                if task.repeat_interval > 0:
                    task.status = TaskStatus.SCHEDULED
                    task.scheduled_at = (datetime.now() + timedelta(seconds=task.repeat_interval)).isoformat()
                    task.updated_at = datetime.now().isoformat()
                    self._save_tasks()
            else:
                self._log("未设置心跳触发器，标记任务为完成", "warning")
                self.update_task(task_id, status=TaskStatus.COMPLETED, progress=1.0, result="任务已标记为完成")
                
        except Exception as e:
            self._log(f"任务执行异常: {task_id} - {e}", "error")
            self.update_task(task_id, status=TaskStatus.FAILED, error=str(e)[:1000])
        finally:
            with self._lock:
                if task_id in self._running_tasks:
                    del self._running_tasks[task_id]

    # ========== 清理残留任务 ==========

    def _cleanup_stale_tasks(self):
        with self._lock:
            stale_tasks = [t for t in self._tasks.values() if t.status == TaskStatus.RUNNING]
            for task in stale_tasks:
                task.status = TaskStatus.PENDING
                task.updated_at = datetime.now().isoformat()
                self._log(f"清理残留运行任务: {task.id} - {task.name}")
            if stale_tasks:
                self._save_tasks()

    # ========== 执行频率检查 ==========

    def _check_execution_frequency(self, task_id: str) -> bool:
        with self._history_lock:
            if task_id not in self._execution_history:
                self._execution_history[task_id] = []
            
            history = self._execution_history[task_id]
            now = datetime.now()
            cutoff = now - timedelta(hours=1)
            history = [t for t in history if datetime.fromisoformat(t) > cutoff]
            self._execution_history[task_id] = history
            
            if len(history) >= 20:
                self._log(f"任务 {task_id} 执行过于频繁（1小时内{len(history)}次）", "warning")
                return False
            
            if history:
                last_time = datetime.fromisoformat(history[-1])
                if (now - last_time).total_seconds() < CODE_TASK_MIN_INTERVAL:
                    self._log(f"任务 {task_id} 执行间隔过短", "warning")
                    return False
            
            history.append(now.isoformat())
            self._execution_history[task_id] = history
            return True

    # ========== 执行代码任务（使用外部沙盒） ==========

    def _execute_code_task(self, task_id: str):
        task = self.get_task(task_id)
        if not task:
            self._log(f"代码任务不存在: {task_id}", "error")
            return
        
        if not task.code_to_execute:
            self._log(f"代码任务无代码: {task_id}", "error")
            self.update_task(task_id, status=TaskStatus.FAILED, error="任务无代码")
            return
        
        # 循环防护
        with self._code_exec_lock:
            if self._global_code_exec_count >= CODE_TASK_GLOBAL_LIMIT:
                self._log(f"全局代码执行次数已达上限", "error")
                self.update_task(task_id, status=TaskStatus.FAILED, 
                               error=f"全局执行次数已达上限 {CODE_TASK_GLOBAL_LIMIT}")
                return
        
        if not self._check_execution_frequency(task_id):
            return
        
        if task.auto_execute_count >= CODE_TASK_EXECUTION_LIMIT:
            self._log(f"代码任务达到硬执行上限", "error")
            self.update_task(task_id, status=TaskStatus.FAILED, 
                           error=f"达到硬执行上限 {CODE_TASK_EXECUTION_LIMIT}")
            return
        
        try:
            self._log(f"执行代码任务: {task_id} - {task.name} (第 {task.auto_execute_count + 1} 次)")
            
            task.status = TaskStatus.RUNNING
            task.updated_at = datetime.now().isoformat()
            self._save_tasks()
            
            # ===== 使用外部沙盒执行 =====
            if self.sandbox:
                stdout, stderr, success = self.sandbox.execute(task.code_to_execute)
            else:
                self._log("沙盒未初始化，无法执行代码任务", "error")
                self.update_task(task_id, status=TaskStatus.FAILED, error="沙盒未初始化")
                return
            
            task.auto_execute_count += 1
            task.last_executed_at = datetime.now().isoformat()
            task.execution_result = stdout if stdout else stderr if stderr else "执行成功"
            
            with self._code_exec_lock:
                self._global_code_exec_count += 1

            if success:
                if task.max_auto_execute > 0:
                    task.progress = min(1.0, task.auto_execute_count / task.max_auto_execute)
                else:
                    task.progress = 0.5
                
                # ===== 根据是否有重复间隔决定状态 =====
                if task.repeat_interval > 0:
                    # 循环任务：调度到下一次执行
                    task.status = TaskStatus.SCHEDULED
                    task.scheduled_at = (datetime.now() + timedelta(seconds=task.repeat_interval)).isoformat()
                else:
                    # 一次性任务：标记完成并从列表中移除
                    task.status = TaskStatus.COMPLETED
                    task.completed_at = datetime.now().isoformat()
                    self._remove_task_from_list(task)
                
                task.result = f"执行成功 (第{task.auto_execute_count}次)"
                if stdout:
                    task.result += f": {stdout[:200]}"
            else:
                task.status = TaskStatus.FAILED
                task.error = stderr[:500] if stderr else "未知错误"
                task.result = f"执行失败: {task.error}"
                self._log(f"代码任务执行失败: {task_id} - {task.error}", "error")
                self._remove_task_from_list(task)
            
            task.updated_at = datetime.now().isoformat()
            self._save_tasks()
            
            self._inject_code_result_to_ai(task)
            
        except Exception as e:
            self._log(f"代码任务执行异常: {task_id} - {e}", "error")
            self._remove_task_from_list(task)

    def _inject_code_result_to_ai(self, task: MissionTask):
        if not self._ai_callback:
            self._log("无AI回调，跳过结果注入", "warning")
            return
        
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            status_text = "成功" if task.status != TaskStatus.FAILED else "失败"
            
            result_preview = task.execution_result[:300] if task.execution_result else '无输出'
            if len(task.execution_result) > 300:
                result_preview += "...(已截断)"
            
            msg = f"""[定时代码任务] {task.name} (ID: {task.id})

执行时间: {timestamp}
执行次数: 第 {task.auto_execute_count} 次
状态: {status_text}
结果预览:
{result_preview}
"""
            if task.error:
                msg += f"\n错误信息:\n{task.error[:200]}"
            
            # ===== 关键：告诉 AI 任务已被移除 =====
            if task.status == TaskStatus.FAILED:
                msg += "\n\n该任务已因执行失败自动移除，如需重试请重新创建任务。"
            elif task.status == TaskStatus.COMPLETED:
                msg += "\n\n任务已完成并移除。"
            
            if task.auto_execute_count >= 10:
                msg += f"\n\n该任务已执行 {task.auto_execute_count} 次，请注意避免无限循环。"
            
            self._ai_callback(msg)
            self._log(f"代码任务{task.id}执行结果已返回AI")
        except Exception as e:
            self._log(f"结果注入失败: {e}", "error")

    def set_ai_callback(self, callback: Callable[[str], None]):
        self._ai_callback = callback

    # ========== 调度器 ==========

    def start_scheduler(self):
        if self._running:
            return
        self._running = True
        self._scheduler_thread = threading.Thread(target=self._scheduler_loop, daemon=True)
        self._scheduler_thread.start()
        self._log("调度器已启动")

    def stop_scheduler(self):
        self._running = False
        if self._scheduler_thread:
            self._scheduler_thread.join(timeout=5)
        self._log("调度器已停止")

    def _scheduler_loop(self):
        while self._running:
            try:
                self._check_scheduled_tasks()
                self._check_pending_tasks()
                self._check_code_tasks()
                time.sleep(SCHEDULE_CHECK_INTERVAL)
            except Exception as e:
                self._log(f"调度器循环异常: {e}", "error")
                time.sleep(SCHEDULE_CHECK_INTERVAL)

    def _get_running_count(self) -> int:
        with self._lock:
            return sum(1 for t in self._tasks.values() if t.status == TaskStatus.RUNNING)

    def _check_scheduled_tasks(self):
        now = datetime.now().isoformat()
        scheduled = self.get_scheduled_tasks()
        
        if self._get_running_count() >= MAX_CONCURRENT_TASKS:
            return

        for task in scheduled:
            if task.task_type == TaskType.CODE:
                continue
            if task.scheduled_at and task.scheduled_at <= now:
                if self._get_running_count() >= MAX_CONCURRENT_TASKS:
                    break
                self._log(f"调度任务触发: {task.id} - {task.name}")
                self.update_task(task.id, status=TaskStatus.PENDING)
                if self.trigger_heartbeat:
                    self._trigger_heartbeat_for_task(task.id)

    def _check_pending_tasks(self):
        pending = self.get_pending_tasks()
        auto_tasks = [
            t for t in pending 
            if t.priority in [TaskPriority.HIGH, TaskPriority.CRITICAL]
            and t.task_type != TaskType.CODE
        ]
        if not auto_tasks:
            return

        for task in auto_tasks:
            if self._get_running_count() >= MAX_CONCURRENT_TASKS:
                break
            deps_ready = all(
                self.get_task(dep_id) and self.get_task(dep_id).status == TaskStatus.COMPLETED
                for dep_id in task.dependencies
            )
            if deps_ready:
                self._log(f"自动执行高优先级任务: {task.id} - {task.name}")
                self.execute_task(task.id, async_mode=True)

    def _check_code_tasks(self):
        with self._lock:
            code_tasks = [
                t for t in self._tasks.values() 
                if t.task_type == TaskType.CODE 
                and t.status in [TaskStatus.PENDING, TaskStatus.SCHEDULED]
                and t.execute_on_schedule
                and (t.max_auto_execute == 0 or t.auto_execute_count < t.max_auto_execute)
            ]
        
        if not code_tasks:
            return
        
        now = datetime.now()
        for task in code_tasks:
            if task.scheduled_at:
                try:
                    if len(task.scheduled_at) <= 8:
                        hour, minute = map(int, task.scheduled_at.split(':')[:2])
                        scheduled_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                        if scheduled_time <= now:
                            scheduled_time = scheduled_time.replace(day=now.day + 1)
                    else:
                        scheduled_time = datetime.fromisoformat(task.scheduled_at)
                    if scheduled_time > now:
                        continue
                except:
                    pass
            
            if task.repeat_interval > 0 and task.last_executed_at:
                try:
                    last_time = datetime.fromisoformat(task.last_executed_at)
                    if (now - last_time).total_seconds() < task.repeat_interval:
                        continue
                except:
                    pass
            
            if self._get_running_count() >= MAX_CONCURRENT_TASKS:
                break
            
            self._log(f"调度代码任务: {task.id} - {task.name}")
            threading.Thread(target=self._execute_code_task, args=(task.id,), daemon=True).start()

    # ========== 统计 ==========

    def get_execution_stats(self) -> Dict:
        with self._code_exec_lock:
            global_count = self._global_code_exec_count
        with self._history_lock:
            history_count = sum(len(h) for h in self._execution_history.values())
        with self._lock:
            task_count = len(self._tasks)
            code_tasks = sum(1 for t in self._tasks.values() if t.task_type == TaskType.CODE)
        return {
            "global_executions": global_count,
            "history_entries": history_count,
            "total_tasks": task_count,
            "code_tasks": code_tasks,
            "max_global_limit": CODE_TASK_GLOBAL_LIMIT,
            "max_per_task_limit": CODE_TASK_EXECUTION_LIMIT
        }

    def reset_execution_counters(self):
        with self._code_exec_lock:
            self._global_code_exec_count = 0
        with self._history_lock:
            self._execution_history.clear()
        self._log("执行计数器已重置")


# ============================================================================
# 外部接口（保持向后兼容）
# ============================================================================

_global_manager: Optional[MissionManager] = None

def get_manager(trigger_heartbeat: Callable = None, logger: Callable = None, sandbox: Any = None) -> MissionManager:
    global _global_manager
    if _global_manager is None:
        _global_manager = MissionManager(trigger_heartbeat=trigger_heartbeat, logger=logger, sandbox=sandbox)
    return _global_manager


def init_manager(trigger_heartbeat: Callable = None, 
                 logger: Callable = None, 
                 sandbox: Any = None) -> MissionManager:
    """初始化任务管理器（sandbox 为可选参数，保持向后兼容）"""
    global _global_manager
    _global_manager = MissionManager(trigger_heartbeat=trigger_heartbeat, 
                                      logger=logger, 
                                      sandbox=sandbox)
    _global_manager.start_scheduler()
    return _global_manager


def shutdown_manager():
    global _global_manager
    if _global_manager:
        _global_manager.stop_scheduler()
        _global_manager = None


def get_tasks_for_prompt() -> str:
    manager = get_manager()
    return manager.get_tasks_for_prompt()


def create_task_from_ai(ai_response: Dict) -> Optional[MissionTask]:
    """
    从 AI 响应创建任务
    
    AI 响应格式（支持 AI 任务和代码任务）：
    {
        "action": "CREATE_TASK",
        "payload": {
            "name": "任务名称",
            "description": "任务描述",
            "priority": "high",
            "scheduled_at": "2024-01-01T10:00:00",
            "repeat_interval": 3600,
            "tags": ["tag1", "tag2"],
            "dependencies": ["task_id_1"],
            "metadata": {...},
            // 代码任务专用
            "task_type": "code",
            "code": "print('hello')",
            "max_auto_execute": 10
        }
    }
    """
    manager = get_manager()
    try:
        payload = ai_response.get('payload', {})
        if not payload.get('name'):
            manager._log("AI创建任务失败: 缺少任务名称", "error")
            return None

        # 检测是否为代码任务
        task_type = payload.get('task_type', TaskType.AI)
        code = payload.get('code', '')
        
        if task_type == TaskType.CODE or code:
            # 代码任务
            return manager.create_task(
                name=payload['name'],
                description=payload.get('description', ''),
                priority=payload.get('priority', TaskPriority.NORMAL),
                scheduled_at=payload.get('scheduled_at', ''),
                repeat_interval=payload.get('repeat_interval', 0),
                max_repeats=payload.get('max_repeats', 0),
                tags=payload.get('tags', []),
                dependencies=payload.get('dependencies', []),
                metadata=payload.get('metadata', {}),
                task_type=TaskType.CODE,
                code=code,
                max_auto_execute=payload.get('max_auto_execute', 0)
            )
        else:
            # AI 任务
            return manager.create_task(
                name=payload['name'],
                description=payload.get('description', ''),
                priority=payload.get('priority', TaskPriority.NORMAL),
                scheduled_at=payload.get('scheduled_at', ''),
                repeat_interval=payload.get('repeat_interval', 0),
                max_repeats=payload.get('max_repeats', 0),
                tags=payload.get('tags', []),
                dependencies=payload.get('dependencies', []),
                metadata=payload.get('metadata', {})
            )
    except Exception as e:
        manager._log(f"AI创建任务失败: {e}", "error")
        return None


def update_task_from_ai(task_id: str, ai_response: Dict) -> Optional[MissionTask]:
    """
    从 AI 响应更新任务
    
    AI 响应格式：
    {
        "action": "UPDATE_TASK",
        "payload": {
            "task_id": "xxx",
            "status": "completed",
            "progress": 0.5,
            "result": "执行结果"
        }
    }
    """
    manager = get_manager()
    try:
        payload = ai_response.get('payload', {})
        update_data = {}
        for key in ['name', 'description', 'status', 'priority', 'progress', 
                    'result', 'error', 'scheduled_at', 'repeat_interval']:
            if key in payload:
                update_data[key] = payload[key]
        if not update_data:
            manager._log("AI更新任务失败: 无有效更新字段", "error")
            return None
        task = manager.update_task(task_id, **update_data)
        if task:
            manager._log(f"AI更新任务: {task_id} - {task.name}")
        return task
    except Exception as e:
        manager._log(f"AI更新任务失败: {e}", "error")
        return None


def delete_task_from_ai(task_id: str, ai_response: Dict = None) -> bool:
    """
    从 AI 响应删除任务
    
    AI 响应格式：
    {
        "action": "DELETE_TASK",
        "payload": {
            "task_id": "xxx",
            "archive": true  // 是否归档
        }
    }
    """
    manager = get_manager()
    try:
        archive = True
        if ai_response:
            payload = ai_response.get('payload', {})
            archive = payload.get('archive', True)
        result = manager.delete_task(task_id, archive=archive)
        if result:
            manager._log(f"AI删除任务: {task_id} (archive={archive})")
        return result
    except Exception as e:
        manager._log(f"AI删除任务失败: {e}", "error")
        return False
