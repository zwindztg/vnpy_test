#!/usr/bin/env bash

set -euo pipefail

# 先尝试正常结束旧的 vn.py GUI 进程。
pkill -TERM -f "Python run_vnpy.py" || true

# 给系统一点时间回收旧窗口。
sleep 1

# 如果还有残留实例，直接强制结束，避免卡在“确认退出”对话框。
pkill -KILL -f "Python run_vnpy.py" || true

# 再等一小下，确保系统把旧窗口句柄完全回收。
sleep 1

# 启动新的 GUI 进程。
exec .venv/bin/python run_vnpy.py
