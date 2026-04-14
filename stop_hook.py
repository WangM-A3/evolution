"""
evolution/stop_hook.py
======================
Stop Hook机制（Stop Hook）

核心功能：任务完成后自动触发验证，失败则继续执行（执着模式）

灵感来源：
  - Ralph Wigum执着模式：做一件事不成功就反复做，直到成功
  - "Stop Hook"：在每个Stop点插入验证，未通过则触发重试

三种Hook模式：
  AUTO_STOP   - 自动停止：验证失败则停止（安全优先）
  PERSISTENT  - 执着模式：验证失败则重试最多N次（可靠性优先）
  ADAPTIVE    - 自适应：动态调整重试策略，基于失败原因选择

工作流程：
  任务完成 → Trigger Hook → 验证 → PASS? → 结束
                              ↓ FAIL
                     Hook模式分支：
                       AUTO_STOP → 停止，报告失败
                       PERSISTENT → 重试（≤max_attempts）→ 验证
                       ADAPTIVE → 分析失败原因 → 决定重试/升级/放弃

使用示例：
    hook = StopHook(
        mode=HookMode.PERSISTENT,
        max_attempts=3,
        verifier=IndependentVerifier(),
    )

    result = hook.after_task(
        task_id="TASK-001",
        goal="实现登录",
        deliverables={"file_path": "auth/login.py"},
        criteria=default_criteria(),
    )
    if result.final_status == "passed":
        print("任务验收通过")
    else:
        print(f"最终失败: {result.final_status}, 尝试次数: {result.attempt_count}")
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import Optional, Any

from evolution.verifier import IndependentVerifier, Criterion
from evolution.verification_report import VerificationReport, ReportStatus


# ─────────────────────────────────────────────
# 枚举定义
# ─────────────────────────────────────────────

class HookMode(Enum):
    """
    Hook执行模式

    AUTO_STOP   - 一票否决：验证失败即停止，适合危险操作
    PERSISTENT  - 执着重试：反复重试直到通过或达到上限
    ADAPTIVE    - 智能适应：根据失败类型动态选择策略
    """
    AUTO_STOP   = auto()   # 安全优先
    PERSISTENT  = auto()   # 可靠性优先
    ADAPTIVE    = auto()   # 动态调整


class HookEvent(Enum):
    TASK_COMPLETED   = auto()   # 任务完成
    TASK_FAILED      = auto()   # 任务失败
    VERIFICATION_RUN = auto()   # 验证运行
    VERIFICATION_PASS = auto()  # 验证通过
    VERIFICATION_FAIL = auto()  # 验证失败
    MAX_RETRIES_HIT  = auto()   # 达到最大重试次数
    STOP_REQUESTED   = auto()   # 主动停止


@dataclass
class HookAttempt:
    """单次Hook尝试"""
    attempt: int
    timestamp: str
    verification_report: Optional[VerificationReport]
    duration_ms: float
    status: str                    # "passed" | "failed" | "error"
    reason: str = ""


@dataclass
class HookResult:
    """Hook执行结果"""
    task_id: str
    final_status: str              # "passed" | "failed" | "max_retries" | "stopped" | "error"
    attempt_count: int
    total_duration_ms: float
    attempts: list[HookAttempt]
    final_report: Optional[VerificationReport]
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "final_status": self.final_status,
            "attempt_count": self.attempt_count,
            "total_duration_ms": self.total_duration_ms,
            "final_verdict": self.final_report.verdict if self.final_report else "",
            "attempts_summary": [
                {"attempt": a.attempt, "status": a.status, "duration_ms": a.duration_ms}
                for a in self.attempts
            ],
            "error": self.error,
        }


# ─────────────────────────────────────────────
# Stop Hook
# ─────────────────────────────────────────────

class StopHook:
    """
    Stop Hook机制

    设计要点：
      - 在任务完成点插入验证（after_task）
      - 支持三种模式：安全停止、执着重试、自适应
      - 每次重试有冷却期（避免死循环）
      - 验证结果自动持久化

    Attributes:
        mode: Hook执行模式
        max_attempts: 最大尝试次数（PERSISTENT模式）
        cooldown_seconds: 重试冷却期（秒）
        verifier: 独立验证器实例
        hook_log: Hook执行日志路径
    """

    def __init__(
        self,
        mode: HookMode = HookMode.PERSISTENT,
        max_attempts: int = 3,
        cooldown_seconds: float = 2.0,
        verifier: Optional[IndependentVerifier] = None,
        hook_log: str = "evolution/stop_hook_log.jsonl",
    ):
        self.mode = mode
        self.max_attempts = max_attempts
        self.cooldown_seconds = cooldown_seconds
        self.verifier = verifier or IndependentVerifier()
        self.hook_log = Path(hook_log)
        self.hook_log.parent.mkdir(parents=True, exist_ok=True)
        if not self.hook_log.exists():
            self.hook_log.write_text("", encoding="utf-8")

    # ── 主入口 ────────────────────────────────────────────────────────────────

    def after_task(
        self,
        task_id: str,
        goal: str,
        deliverables: Optional[dict[str, Any]] = None,
        criteria: Optional[list[Criterion]] = None,
        context: Optional[dict[str, Any]] = None,
    ) -> HookResult:
        """
        任务完成后执行Hook验证

        Args:
            task_id: 任务ID
            goal: 任务目标
            deliverables: 交付物上下文
            criteria: 验收标准（默认使用default_criteria）
            context: 额外上下文

        Returns:
            HookResult（包含所有尝试记录）
        """
        context = context or {}
        deliverables = deliverables or {}
        start_time = time.time()
        attempts: list[HookAttempt] = []
        attempt_num = 0

        # 构建标准
        if criteria is None:
            criteria = self.verifier.default_criteria_for_task(task_id)
        criteria_map = {c.name: c for c in criteria}

        while True:
            attempt_num += 1
            attempt_start = time.time()
            report: Optional[VerificationReport] = None
            status = "error"
            reason = ""

            try:
                report = self.verifier.verify(
                    task_id=task_id,
                    goal=goal,
                    criteria=criteria,
                    deliverables=deliverables,
                )
                if report.status == ReportStatus.PASSED:
                    status = "passed"
                    reason = report.verdict
                else:
                    status = "failed"
                    reason = report.verdict
            except Exception as e:
                status = "error"
                reason = f"验证器异常: {type(e).__name__}: {e}"

            duration_ms = (time.time() - attempt_start) * 1000
            attempt_record = HookAttempt(
                attempt=attempt_num,
                timestamp=datetime.utcnow().isoformat(),
                verification_report=report,
                duration_ms=duration_ms,
                status=status,
                reason=reason,
            )
            attempts.append(attempt_record)

            # 记录到日志
            self._log_event(task_id, attempt_record)

            # 判断是否终止
            if status == "passed":
                return HookResult(
                    task_id=task_id,
                    final_status="passed",
                    attempt_count=attempt_num,
                    total_duration_ms=(time.time() - start_time) * 1000,
                    attempts=attempts,
                    final_report=report,
                )

            if self.mode == HookMode.AUTO_STOP:
                return HookResult(
                    task_id=task_id,
                    final_status="failed",
                    attempt_count=attempt_num,
                    total_duration_ms=(time.time() - start_time) * 1000,
                    attempts=attempts,
                    final_report=report,
                    error=reason,
                )

            if attempt_num >= self.max_attempts:
                return HookResult(
                    task_id=task_id,
                    final_status="max_retries",
                    attempt_count=attempt_num,
                    total_duration_ms=(time.time() - start_time) * 1000,
                    attempts=attempts,
                    final_report=report,
                    error=reason,
                )

            # PERSISTENT / ADAPTIVE: 重试前冷却
            if self.mode == HookMode.ADAPTIVE:
                adaptive_action = self._adaptive_decide(report, context)
                if adaptive_action == "retry":
                    self._cooldown(attempt_num)
                    continue
                elif adaptive_action == "escalate":
                    return HookResult(
                        task_id=task_id,
                        final_status="escalated",
                        attempt_count=attempt_num,
                        total_duration_ms=(time.time() - start_time) * 1000,
                        attempts=attempts,
                        final_report=report,
                        error=f"升级: {reason}",
                    )
                else:  # abandon
                    return HookResult(
                        task_id=task_id,
                        final_status="abandoned",
                        attempt_count=attempt_num,
                        total_duration_ms=(time.time() - start_time) * 1000,
                        attempts=attempts,
                        final_report=report,
                        error=f"放弃: {reason}",
                    )
            else:
                self._cooldown(attempt_num)

    # ── 自适应决策 ────────────────────────────────────────────────────────────

    def _adaptive_decide(
        self,
        report: Optional[VerificationReport],
        context: dict[str, Any],
    ) -> str:
        """
        自适应决策：根据失败原因决定下一步动作

        Returns:
            "retry"      - 重试
            "escalate"   - 升级（通知人工介入）
            "abandon"    - 放弃（记录为已知限制）
        """
        if report is None:
            return "escalate"

        # 统计失败分类
        failed = [c for c in report.checks if not c.passed]
        critical = [c for c in failed if c.severity >= 4]
        performance = [c for c in failed if c.severity <= 2]

        # 阻断级失败 → 升级人工
        if critical:
            return "escalate"

        # 性能/风格类失败 → 重试（有优化空间）
        if performance or all(c.severity <= 3 for c in failed):
            return "retry"

        # 一般失败 → 有限重试
        return "retry"

    # ── 冷却 ─────────────────────────────────────────────────────────────────

    def _cooldown(self, attempt_num: int):
        """重试冷却期（指数退避）"""
        import math
        backoff = min(self.cooldown_seconds * (2 ** (attempt_num - 1)), 30.0)
        time.sleep(backoff)

    # ── 日志 ─────────────────────────────────────────────────────────────────

    def _log_event(self, task_id: str, attempt: HookAttempt):
        """写入Hook执行日志"""
        entry = {
            "task_id": task_id,
            "attempt": attempt.attempt,
            "timestamp": attempt.timestamp,
            "status": attempt.status,
            "duration_ms": attempt.duration_ms,
            "reason": attempt.reason,
            "hook_mode": self.mode.name,
        }
        with open(self.hook_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def get_hook_history(self, task_id: Optional[str] = None, limit: int = 20) -> list[dict]:
        """读取Hook历史"""
        if not self.hook_log.exists():
            return []
        lines = self.hook_log.read_text(encoding="utf-8").strip().splitlines()
        records = [json.loads(l) for l in lines if l.strip()]
        if task_id:
            records = [r for r in records if r.get("task_id") == task_id]
        return records[-limit:]

    # ── 便捷入口 ──────────────────────────────────────────────────────────────

    def verify_quick(
        self,
        task_id: str,
        deliverables: Optional[dict[str, Any]] = None,
    ) -> VerificationReport:
        """
        快速验证（单次，不重试）
        等价于 AUTO_STOP + max_attempts=1
        """
        criteria = self.verifier.default_criteria_for_task(task_id)
        return self.verifier.verify(
            task_id=task_id,
            goal="快速验证",
            criteria=criteria,
            deliverables=deliverables or {},
        )
