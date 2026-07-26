# watchdog.py - 改为线程版本

import threading
import time
import os
import psutil
from datetime import datetime

class WatchdogThread(threading.Thread):
    """看门狗线程 - 内置在主进程中"""
    
    def __init__(self, check_interval=60, timeout_minutes=15, grace_period=120):
        super().__init__(daemon=True)
        self.check_interval = check_interval
        self.timeout_minutes = timeout_minutes
        self.grace_period = grace_period
        self.running = False
        self.heartbeat_file = "heartbeat.txt"
        self.start_time = time.time()
    
    def run(self):
        self.running = True
        print(f"[看门狗] 内置监控已启动 (间隔:{self.check_interval}s, 超时:{self.timeout_minutes}分钟)")
        
        while self.running:
            time.sleep(self.check_interval)
            
            # 检查是否在宽限期内
            if time.time() - self.start_time < self.grace_period:
                continue
            
            # 检查心跳
            if not self._check_heartbeat():
                print(f"[看门狗] 检测到无响应，正在重启...")
                # 在 EXE 中重启自身
                self._restart_self()
                break
    
    def _check_heartbeat(self):
        if not os.path.exists(self.heartbeat_file):
            return False
        
        try:
            mtime = os.path.getmtime(self.heartbeat_file)
            if (time.time() - mtime) / 60 > self.timeout_minutes:
                return False
            return True
        except:
            return False
    
    def _restart_self(self):
        """重启当前进程"""
        import sys
        import subprocess
        
        # 保存当前 PID 到文件，供新进程清理
        with open("old_pid.txt", "w") as f:
            f.write(str(os.getpid()))
        
        # 启动新进程
        if getattr(sys, 'frozen', False):
            subprocess.Popen([sys.executable], creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            subprocess.Popen([sys.executable, __file__])
        
        # 退出当前进程
        os._exit(0)
    
    def stop(self):
        self.running = False
