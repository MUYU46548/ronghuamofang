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

TOKEN_IN_RE = re.compile(
    r"(?:tokens_in|prompt_tokens|input_tokens|input)[\s:=]+([\d,]+)", re.IGNORECASE)
TOKEN_OUT_RE = re.compile(
    r"(?:tokens_out|completion_tokens|output_tokens|output)[\s:=]+([\d,]+)", re.IGNORECASE)
TOKEN_TOTAL_RE = re.compile(
    r"(?:total_tokens|tokens)[\s:=]+([\d,]+)", re.IGNORECASE)
COST_RE = re.compile(r"cost[\s:=]+([\d.]+)", re.IGNORECASE)


def _to_int(s):
    try:
        return int(s.replace(",", ""))
    except (ValueError, AttributeError):
        return 0


def parse_usage(stdout):
    """从子会话 stdout 解析 (tokens_in, tokens_out, cost_yuan, estimated)。

    解析不到 token 时按任务文件无法获知——调用方需提供估算（见 estimate_tokens）。
    返回 estimated=False 表示来自真实解析。
    """
    m_in = TOKEN_IN_RE.search(stdout)
    m_out = TOKEN_OUT_RE.search(stdout)
    m_total = TOKEN_TOTAL_RE.search(stdout)
    m_cost = COST_RE.search(stdout)

    tokens_in = _to_int(m_in.group(1)) if m_in else 0
    tokens_out = _to_int(m_out.group(1)) if m_out else 0
    if tokens_in == 0 and tokens_out == 0 and m_total:
        total = _to_int(m_total.group(1))
        tokens_in = total * 3 // 4
        tokens_out = total - tokens_in
    cost_yuan = float(m_cost.group(1)) if m_cost else 0.0
    estimated = not (m_in or m_out or m_total) or cost_yuan == 0.0
    return tokens_in, tokens_out, cost_yuan, estimated


def estimate_tokens(task_path):
    """解析失败时的保守估算：任务文件字符量 → token。

    中文约 1 token/字（UTF-8 3 字节/字）。输入按文件大小×0.4，输出按输入 1/3。
    返回 (tokens_in, tokens_out)。仅用于记账兜底，标注 estimated=True。
    """
    try:
        size = Path(task_path).stat().st_size
    except OSError:
        size = 0
    tokens_in = max(1000, int(size * 0.4))
    tokens_out = max(500, tokens_in // 3)
    return tokens_in, tokens_out


class HermesClient:
    def __init__(self, hermes_bin="hermes", timeout=900, model=None):
        self.hermes_bin = hermes_bin
        self.timeout = timeout
        self.model = model  # 阶段模型路由（-m 参数）；None = 用 Hermes 默认

    def run_task(self, task_file, workdir=None, model=None):
        """运行一个自包含任务文件。

        返回 dict: {exit_code, stdout_tail, tokens, cost_yuan, estimated}
        tokens/cost 优先从 stdout 解析；解析不到时按任务文件大小保守估算，
        estimated=True 标记（避免假 0 让预算熔断失效）。
        """
        task_path = Path(task_file)
        cmd = [
            self.hermes_bin, "chat", "-q",
            f"阅读并严格按 {task_path} 中的指示执行全部步骤。完成后简要汇报：产物路径、校验结果、遇到的问题。",
        ]
        eff_model = model or self.model
        if eff_model:
            cmd += ["-m", eff_model]
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=self.timeout, cwd=workdir)
        stdout = proc.stdout or ""
        tokens_in, tokens_out, cost_yuan, estimated = parse_usage(stdout)
        if estimated:
            est_in, est_out = estimate_tokens(task_path)
            tokens_in, tokens_out = est_in, est_out
        return {
            "exit_code": proc.returncode,
            "stdout_tail": stdout[-2000:],
            "tokens": tokens_in,
            "tokens_out": tokens_out,
            "cost_yuan": cost_yuan,
            "estimated": estimated,
        }

    def write_task(self, task_dir, name, content):
        """写入任务文件并返回路径。"""
        task_dir = Path(task_dir)
        task_dir.mkdir(parents=True, exist_ok=True)
        path = task_dir / name
        write_text(path, content)
        return path
