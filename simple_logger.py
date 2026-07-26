#!/usr/bin/env python3
"""
简单日志模块
用法: 
    from simple_logger import log
    log("要记录的内容")
"""

import os
import threading
from datetime import datetime
from pathlib import Path


class SimpleLogger:
    """简单日志记录器 - 优先填充已有文件，满了再按时间创建新文件"""
    
    _instance = None
    _lock = threading.Lock()  # ← 新增：类级别线程锁
    
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self, log_dir: str = "LOGS", max_size_mb: int = 0.5):
        if hasattr(self, '_initialized'):
            return
        self._initialized = True
        
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.max_size = max_size_mb * 1024 * 1024
        
        # 启动时查找可用文件
        self._current_log_file = self._find_available_file()
        if self._current_log_file is None:
            # 没有可用文件，创建新文件
            self._current_log_file = self._get_new_log_file()
    
    def log(self, content: str):  # ← 参数名保持 content 不变
        """写入日志 - 自动加时间戳和换行"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_line = f"[{timestamp}] {content}\n"
        
        with SimpleLogger._lock:  # ← 新增：保证并发安全
            # 检查当前文件是否已满
            if self._current_log_file.exists() and self._current_log_file.stat().st_size > self.max_size:
                # 当前文件满了，找下一个可用文件
                available_file = self._find_available_file()
                if available_file is not None:
                    self._current_log_file = available_file
                else:
                    # 所有文件都满了，创建新文件
                    self._current_log_file = self._get_new_log_file()
            
            try:
                with open(self._current_log_file, 'a', encoding='utf-8') as f:
                    f.write(log_line)
                    f.flush()  # ← 新增：强制刷新缓冲区，确保即时落盘
            except Exception as e:
                # ← 新增：异常不再静默吞掉
                print(f"[LOGGER FATAL] 日志写入失败: {e}")
    
    def _find_available_file(self) -> Path:
        """查找第一个大小小于限制的文件"""
        # 获取所有日志文件（按文件名排序）
        log_files = sorted(self.log_dir.glob("log_*.txt"))
        
        for file in log_files:
            if file.stat().st_size < self.max_size:
                return file
        
        return None  # 没有可用文件
    
    def _get_new_log_file(self) -> Path:
        """按当前时间创建新日志文件"""
        date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        new_file = self.log_dir / f"log_{date_str}.txt"
        return new_file


# ========== 导出函数（推荐使用方式） ==========

_logger = SimpleLogger()

def log(content: str):  # ← 参数名保持 content 不变
    """最简日志函数 - 直接调用即可"""
    _logger.log(content)


# ========== 测试 ==========

if __name__ == "__main__":
    log("日志模块测试开始")
    log("这是一条普通日志")
    log("这是一条警告")
    log("这是一条错误")
    print("测试完成！")
