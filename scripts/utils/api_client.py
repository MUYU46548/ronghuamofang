# -*- coding: utf-8 -*-
"""Hermes 子会话调用封装。

设计要点（架构文档 v2 2.3/2.4）：
- 任务说明写入自包含 task 文件（data/state/tasks/），子会话用工具读取并执行，
  规避 Windows 命令行长度限制，也利用 Hermes 的文件工具能力；
- 子会话为全新独立会话（hermes chat -q），任务文件必须自包含全部上下文。
"""
import re
import subprocess
from pathlib import Path

from utils.file_io import write_text

TOKEN_RE = re.compile(r"tokens?[\s:=]+([\d,]+)", re.IGNORECASE)
COST_RE = re.compile(r"cost[\s:=]+([\d.]+)", re.IGNORECASE)


class HermesClient:
    def __init__(self, hermes_bin="hermes", timeout=900):
        self.hermes_bin = hermes_bin
        self.timeout = timeout

    def run_task(self, task_file, workdir=None):
        """运行一个自包含任务文件。

        返回 dict: {exit_code, stdout_tail, tokens, cost_yuan}
        tokens/cost 尝试从 stdout 解析（Hermes 未输出时记 0，P1 接入用量统计）。
        """
        task_path = Path(task_file)
        cmd = [
            self.hermes_bin, "chat", "-q",
            f"阅读并严格按 {task_path} 中的指示执行全部步骤。完成后简要汇报：产物路径、校验结果、遇到的问题。",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=self.timeout, cwd=workdir)
        stdout = proc.stdout or ""
        tokens_m = TOKEN_RE.search(stdout)
        cost_m = COST_RE.search(stdout)
        return {
            "exit_code": proc.returncode,
            "stdout_tail": stdout[-2000:],
            "tokens": int(tokens_m.group(1).replace(",", "")) if tokens_m else 0,
            "cost_yuan": float(cost_m.group(1)) if cost_m else 0.0,
        }

    def write_task(self, task_dir, name, content):
        """写入任务文件并返回路径。"""
        task_dir = Path(task_dir)
        task_dir.mkdir(parents=True, exist_ok=True)
        path = task_dir / name
        write_text(path, content)
        return path
