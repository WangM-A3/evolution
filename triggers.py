"""
evolution/triggers.py
=====================
六类自动触发器（Automatic Triggers）

架构原则：
- 每个触发器独立运行，通过统一的 TriggerResult 接口返回
- 支持 enable/disable、优先级、可配置阈值
- 触发时记录触发原因、上下文快照、建议的进化动作

六类触发器：
  1. PerformanceTrigger  - 性能下降触发（响应时间 > 阈值）
  2. FailureTrigger      - 失败模式触发（连续失败 N 次）
  3. PatternTrigger       - 模式识别触发（相似问题出现 M 次）
  4. FeedbackTrigger      - 用户反馈触发（负反馈累计）
  5. ScheduleTrigger      - 定时巡检触发（每日/每周）
  6. DriftTrigger        - 漂移检测触发（行为偏离基线）
"""

from __future__ import annotations

import time
import json
import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum, auto
from pathlib import Path
from typing import Optional, Any


# ─────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────

class TriggerType(Enum):
    PERFORMANCE = auto()
    FAILURE = auto()
    PATTERN = auto()
    FEEDBACK = auto()
    SCHEDULE = auto()
    DRIFT = auto()


@dataclass
class TriggerResult:
    """统一触发结果"""
    triggered: bool                          # 是否触发
    trigger_type: TriggerType                # 触发器类型
    trigger_reason: str                       # 触发原因（人类可读）
    severity: int                            # 严重程度 1-5
    context: dict[str, Any]                  # 上下文快照
    suggested_action: str                    # 建议的进化动作
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["trigger_type"] = self.trigger_type.name
        return d


# ─────────────────────────────────────────────
# 基类
# ─────────────────────────────────────────────

class BaseTrigger:
    """触发器基类，提供通用接口"""

    def __init__(
        self,
        name: str,
        trigger_type: TriggerType,
        enabled: bool = True,
        priority: int = 5,          # 1=最高, 5=最低
        cool_down_seconds: int = 300,
    ):
        self.name = name
        self.trigger_type = trigger_type
        self.enabled = enabled
        self.priority = priority
        self.cool_down_seconds = cool_down_seconds
        self._last_triggered_at: Optional[float] = None
        self._trigger_count = 0

    def check(self) -> TriggerResult:
        """子类必须实现的检查逻辑"""
        raise NotImplementedError

    def _is_in_cooldown(self) -> bool:
        """检查是否在冷却期内"""
        if self._last_triggered_at is None:
            return False
        return time.time() - self._last_triggered_at < self.cool_down_seconds

    def _not_triggered(self, reason: str) -> TriggerResult:
        """生成未触发的结果"""
        return TriggerResult(
            triggered=False,
            trigger_type=self.trigger_type,
            trigger_reason=reason,
            severity=0,
            context={},
            suggested_action="",
        )

    def _record_trigger(self, result: TriggerResult) -> TriggerResult:
        """记录触发事件"""
        self._last_triggered_at = time.time()
        self._trigger_count += 1
        result.metadata["trigger_count"] = self._trigger_count
        result.metadata["trigger_name"] = self.name
        return result

    def reset_cooldown(self):
        """重置冷却（用于测试或手动重置）"""
        self._last_triggered_at = None


# ─────────────────────────────────────────────
# P0: 性能触发器
# ─────────────────────────────────────────────

@dataclass
class PerformanceState:
    """性能状态追踪"""
    response_times: list[float] = field(default_factory=list)   # 最近 N 次响应时间（秒）
    max_history: int = 100
    p50: float = 0.0
    p95: float = 0.0
    p99: float = 0.0

    def record(self, response_time: float):
        self.response_times.append(response_time)
        if len(self.response_times) > self.max_history:
            self.response_times = self.response_times[-self.max_history:]
        self._recalc()

    def _recalc(self):
        if not self.response_times:
            return
        sorted_times = sorted(self.response_times)
        n = len(sorted_times)
        self.p50 = sorted_times[int(n * 0.50)]
        self.p95 = sorted_times[int(n * 0.95)] if n >= 20 else sorted_times[-1]
        self.p99 = sorted_times[int(n * 0.99)] if n >= 100 else sorted_times[-1]


class PerformanceTrigger(BaseTrigger):
    """
    性能下降触发器

    触发条件（满足任一）：
    - P95 响应时间超过 threshold_p95
    - 最近 10 次平均响应时间超过 threshold_avg10
    - 单次响应时间超过 threshold_single
    """

    def __init__(
        self,
        threshold_p95: float = 5.0,       # P95 阈值（秒）
        threshold_avg10: float = 3.0,     # 最近10次平均阈值（秒）
        threshold_single: float = 10.0,   # 单次最大允许（秒）
        window_size: int = 100,
        enabled: bool = True,
        priority: int = 1,
    ):
        super().__init__(
            name="PerformanceTrigger",
            trigger_type=TriggerType.PERFORMANCE,
            enabled=enabled,
            priority=priority,
        )
        self.threshold_p95 = threshold_p95
        self.threshold_avg10 = threshold_avg10
        self.threshold_single = threshold_single
        self.state = PerformanceState(max_history=window_size)

    def record_response_time(self, response_time: float):
        """外部调用：记录一次响应时间"""
        self.state.record(response_time)

    def check(self) -> TriggerResult:
        if not self.enabled:
            return self._not_triggered("trigger disabled")
        if self._is_in_cooldown():
            return self._not_triggered("in cooldown period")

        reasons = []
        severity = 1
        context: dict[str, Any] = {}

        # 检查 P95
        if self.state.p95 > self.threshold_p95 > 0:
            reasons.append(f"P95={self.state.p95:.2f}s > {self.threshold_p95}s")
            severity = max(severity, 3)
            context["p95"] = round(self.state.p95, 3)

        # 检查最近10次平均
        if len(self.state.response_times) >= 10:
            avg10 = sum(self.state.response_times[-10:]) / 10
            if avg10 > self.threshold_avg10 > 0:
                reasons.append(f"avg10={avg10:.2f}s > {self.threshold_avg10}s")
                severity = max(severity, 2)
                context["avg10"] = round(avg10, 3)

        # 检查单次
        if self.state.response_times and self.state.response_times[-1] > self.threshold_single > 0:
            reasons.append(f"single={self.state.response_times[-1]:.2f}s > {self.threshold_single}s")
            severity = max(severity, 4)
            context["single"] = round(self.state.response_times[-1], 3)

        if reasons:
            return self._record_trigger(TriggerResult(
                triggered=True,
                trigger_type=self.trigger_type,
                trigger_reason="; ".join(reasons),
                severity=severity,
                context={
                    "p50": round(self.state.p50, 3),
                    "p95": round(self.state.p95, 3),
                    "p99": round(self.state.p99, 3),
                    "sample_count": len(self.state.response_times),
                    **{k: v for k, v in context.items()},
                },
                suggested_action=self._suggest_action(severity),
            ))

        return self._not_triggered("all metrics within thresholds")

    def _suggest_action(self, severity: int) -> str:
        if severity >= 4:
            return "CRITICAL: 立即创建检查点，分析最近代码变更，建议回滚到稳定版本"
        elif severity >= 3:
            return "HIGH: P95严重超标，执行诊断循环，生成性能分析报告"
        else:
            return "MEDIUM: 平均响应时间上升，启动性能巡检，监控趋势变化"


# ─────────────────────────────────────────────
# P0: 失败触发器
# ─────────────────────────────────────────────

@dataclass
class FailureState:
    """失败状态追踪"""
    consecutive_failures: int = 0
    total_failures: int = 0
    last_failure_at: Optional[str] = None
    failure_types: dict[str, int] = field(default_factory=dict)  # type -> count
    recent_failures: list[dict] = field(default_factory=list)    # 最近 N 次详情
    max_recent: int = 20

    def record(self, failure_type: str, error_msg: str, context: dict):
        self.consecutive_failures += 1
        self.total_failures += 1
        self.last_failure_at = datetime.utcnow().isoformat()
        self.failure_types[failure_type] = self.failure_types.get(failure_type, 0) + 1
        entry = {
            "type": failure_type,
            "msg": error_msg[:200],
            "ts": self.last_failure_at,
            "ctx": {k: str(v)[:100] for k, v in context.items()},
        }
        self.recent_failures.append(entry)
        if len(self.recent_failures) > self.max_recent:
            self.recent_failures = self.recent_failures[-self.max_recent:]


class FailureTrigger(BaseTrigger):
    """
    失败模式触发器

    触发条件：
    - 连续失败 N 次（consecutive_threshold）
    - 累计失败超过 M 次（total_threshold）
    """

    def __init__(
        self,
        consecutive_threshold: int = 3,
        total_threshold: int = 20,
        time_window_hours: int = 24,
        enabled: bool = True,
        priority: int = 1,
    ):
        super().__init__(
            name="FailureTrigger",
            trigger_type=TriggerType.FAILURE,
            enabled=enabled,
            priority=priority,
        )
        self.consecutive_threshold = consecutive_threshold
        self.total_threshold = total_threshold
        self.time_window_hours = time_window_hours
        self.state = FailureState()

    def record_failure(
        self,
        failure_type: str,
        error_msg: str = "",
        context: Optional[dict] = None,
    ):
        self.state.record(failure_type, error_msg, context or {})

    def check(self) -> TriggerResult:
        if not self.enabled:
            return self._not_triggered("trigger disabled")
        if self._is_in_cooldown():
            return self._not_triggered("in cooldown period")

        reasons = []
        severity = 1

        # 连续失败
        if self.state.consecutive_failures >= self.consecutive_threshold:
            reasons.append(
                f"连续失败 {self.state.consecutive_failures} 次 >= {self.consecutive_threshold} 次"
            )
            severity = max(severity, 4 if self.state.consecutive_failures >= 5 else 3)

        # 累计失败
        if self.state.total_failures >= self.total_threshold:
            reasons.append(f"累计失败 {self.state.total_failures} 次 >= {self.total_threshold} 次")
            severity = max(severity, 2)

        if reasons:
            # 计算最常见失败类型
            top_type = max(
                self.state.failure_types.items(),
                key=lambda x: x[1],
                default=("unknown", 0)
            )
            return self._record_trigger(TriggerResult(
                triggered=True,
                trigger_type=self.trigger_type,
                trigger_reason="; ".join(reasons),
                severity=severity,
                context={
                    "consecutive_failures": self.state.consecutive_failures,
                    "total_failures": self.state.total_failures,
                    "top_failure_type": top_type[0],
                    "top_failure_count": top_type[1],
                    "failure_types": self.state.failure_types,
                    "last_failure_at": self.state.last_failure_at,
                    "recent_sample": self.state.recent_failures[-3:],
                },
                suggested_action=self._suggest_action(severity, top_type[0]),
            ))

        return self._not_triggered("failure count below threshold")

    def reset_consecutive(self):
        """成功后重置连续失败计数"""
        self.state.consecutive_failures = 0

    def _suggest_action(self, severity: int, top_type: str) -> str:
        if severity >= 4:
            return f"HIGHEST: 连续失败严重，启动紧急诊断，归档失败记录到 failures/，生成根因分析"
        return f"MEDIUM: 失败率上升，最常见类型={top_type}，建议检查对应模块日志和最近变更"


# ─────────────────────────────────────────────
# P1: 模式触发器
# ─────────────────────────────────────────────

@dataclass
class PatternState:
    """模式识别状态"""
    issue_fingerprints: dict[str, int] = field(default_factory=dict)  # fingerprint -> count
    recent_issues: list[dict] = field(default_factory=list)
    max_recent: int = 50

    @staticmethod
    def fingerprint(issue_text: str) -> str:
        """生成问题的语义指纹（简化版：关键词排序哈希）"""
        import re
        words = re.findall(r"[\w]+", issue_text.lower())
        core = sorted(set(w for w in words if len(w) > 2))[:10]
        key = "|".join(core)
        return hashlib.md5(key.encode()).hexdigest()[:12]


class PatternTrigger(BaseTrigger):
    """
    模式识别触发器

    触发条件：相同或相似问题（相同指纹）出现 M 次
    """

    def __init__(
        self,
        min_occurrence: int = 3,
        enabled: bool = True,
        priority: int = 2,
    ):
        super().__init__(
            name="PatternTrigger",
            trigger_type=TriggerType.PATTERN,
            enabled=enabled,
            priority=priority,
        )
        self.min_occurrence = min_occurrence
        self.state = PatternState()

    def record_issue(self, issue_text: str, context: Optional[dict] = None):
        fp = self.state.fingerprint(issue_text)
        self.state.issue_fingerprints[fp] = self.state.issue_fingerprints.get(fp, 0) + 1
        self.state.recent_issues.append({
            "fingerprint": fp,
            "text": issue_text[:300],
            "ts": datetime.utcnow().isoformat(),
            "ctx": {k: str(v)[:100] for k, v in (context or {}).items()},
        })
        if len(self.state.recent_issues) > self.state.max_recent:
            self.state.recent_issues = self.state.recent_issues[-self.state.max_recent:]

    def check(self) -> TriggerResult:
        if not self.enabled:
            return self._not_triggered("trigger disabled")
        if self._is_in_cooldown():
            return self._not_triggered("in cooldown period")

        # 找出超过阈值的指纹
        hot_patterns = {
            fp: cnt for fp, cnt in self.state.issue_fingerprints.items()
            if cnt >= self.min_occurrence
        }

        if not hot_patterns:
            return self._not_triggered(f"no pattern with >= {self.min_occurrence} occurrences")

        # 取最热门的模式
        top_fp = max(hot_patterns, key=hot_patterns.get)  # type: ignore
        top_count = hot_patterns[top_fp]
        top_issues = [i for i in self.state.recent_issues if i["fingerprint"] == top_fp]

        severity = min(5, 1 + top_count)  # 出现越多越严重
        return self._record_trigger(TriggerResult(
            triggered=True,
            trigger_type=self.trigger_type,
            trigger_reason=f"模式 '{top_fp}' 出现 {top_count} 次（阈值={self.min_occurrence}）",
            severity=severity,
            context={
                "fingerprint": top_fp,
                "occurrence": top_count,
                "all_hot_patterns": hot_patterns,
                "sample_issues": top_issues[:5],
            },
            suggested_action=f"MEDIUM: 模式重复出现 {top_count} 次，建议聚合并生成规则到 rules/",
        ))


# ─────────────────────────────────────────────
# P1: 反馈触发器
# ─────────────────────────────────────────────

@dataclass
class FeedbackState:
    """反馈状态追踪"""
    negative_count: int = 0
    positive_count: int = 0
    recent_feedback: list[dict] = field(default_factory=list)
    max_recent: int = 30
    negative_sequence: int = 0  # 连续负反馈数

    def record(self, is_negative: bool, feedback_text: str = "", context: Optional[dict] = None):
        entry = {
            "negative": is_negative,
            "text": feedback_text[:200],
            "ts": datetime.utcnow().isoformat(),
            "ctx": context or {},
        }
        self.recent_feedback.append(entry)
        if len(self.recent_feedback) > self.max_recent:
            self.recent_feedback = self.recent_feedback[-self.max_recent:]
        if is_negative:
            self.negative_count += 1
            self.negative_sequence += 1
        else:
            self.negative_sequence = 0
            self.positive_count += 1


class FeedbackTrigger(BaseTrigger):
    """
    用户反馈触发器

    触发条件（满足任一）：
    - 累计负反馈 >= threshold_negative
    - 连续负反馈 >= threshold_sequence
    - 负反馈占比 > ratio_threshold（时间窗口内）
    """

    def __init__(
        self,
        threshold_negative: int = 5,
        threshold_sequence: int = 3,
        ratio_threshold: float = 0.7,
        window_size: int = 20,
        enabled: bool = True,
        priority: int = 2,
    ):
        super().__init__(
            name="FeedbackTrigger",
            trigger_type=TriggerType.FEEDBACK,
            enabled=enabled,
            priority=priority,
        )
        self.threshold_negative = threshold_negative
        self.threshold_sequence = threshold_sequence
        self.ratio_threshold = ratio_threshold
        self.window_size = window_size
        self.state = FeedbackState()

    def record_feedback(self, is_negative: bool, feedback_text: str = "", context: Optional[dict] = None):
        self.state.record(is_negative, feedback_text, context)

    def check(self) -> TriggerResult:
        if not self.enabled:
            return self._not_triggered("trigger disabled")
        if self._is_in_cooldown():
            return self._not_triggered("in cooldown period")

        reasons = []
        severity = 1

        # 累计负反馈
        if self.state.negative_count >= self.threshold_negative:
            reasons.append(f"累计负反馈 {self.state.negative_count} >= {self.threshold_negative}")
            severity = max(severity, 3)

        # 连续负反馈
        if self.state.negative_sequence >= self.threshold_sequence:
            reasons.append(f"连续负反馈 {self.state.negative_sequence} >= {self.threshold_sequence}")
            severity = max(severity, 4)

        # 负反馈占比
        window = self.state.recent_feedback[-self.window_size:]
        if window:
            neg_ratio = sum(1 for f in window if f["negative"]) / len(window)
            if neg_ratio > self.ratio_threshold:
                reasons.append(f"最近负反馈占比 {neg_ratio:.0%} > {self.ratio_threshold:.0%}")
                severity = max(severity, 3)

        if reasons:
            return self._record_trigger(TriggerResult(
                triggered=True,
                trigger_type=self.trigger_type,
                trigger_reason="; ".join(reasons),
                severity=severity,
                context={
                    "negative_count": self.state.negative_count,
                    "positive_count": self.state.positive_count,
                    "negative_sequence": self.state.negative_sequence,
                    "recent_window": len(window),
                    "neg_ratio": round(sum(1 for f in window if f["negative"]) / len(window), 3) if window else 0,
                    "recent_sample": self.state.recent_feedback[-5:],
                },
                suggested_action=self._suggest_action(severity),
            ))

        return self._not_triggered("feedback metrics within thresholds")

    def _suggest_action(self, severity: int) -> str:
        if severity >= 4:
            return "HIGH: 用户满意度显著下降，立即分析最近输出质量变化，必要时回滚行为"
        return "MEDIUM: 负反馈趋势上升，分析反馈内容，识别常见问题类型"


# ─────────────────────────────────────────────
# P1: 定时触发器
# ─────────────────────────────────────────────

class ScheduleType(Enum):
    HOURLY = auto()
    DAILY = auto()
    WEEKLY = auto()


class ScheduleTrigger(BaseTrigger):
    """
    定时巡检触发器

    按固定周期触发，用于主动巡检而非被动响应
    """

    def __init__(
        self,
        schedule_type: ScheduleType = ScheduleType.DAILY,
        hour_of_day: int = 9,        # 每天几点触发（UTC）
        day_of_week: int = 1,        # 周几触发（1=周一）
        enabled: bool = True,
        priority: int = 3,
    ):
        super().__init__(
            name=f"ScheduleTrigger({schedule_type.name})",
            trigger_type=TriggerType.SCHEDULE,
            enabled=enabled,
            priority=priority,
            cool_down_seconds=3600,  # 至少1小时冷却
        )
        self.schedule_type = schedule_type
        self.hour_of_day = hour_of_day
        self.day_of_week = day_of_week
        self._last_scheduled_run: Optional[str] = None

    def check(self) -> TriggerResult:
        if not self.enabled:
            return self._not_triggered("trigger disabled")

        now = datetime.utcnow()
        should_trigger = False
        reason = ""

        if self.schedule_type == ScheduleType.HOURLY:
            if now.minute == 0:
                should_trigger = True
                reason = f"整点巡检 {now.hour}:00 UTC"

        elif self.schedule_type == ScheduleType.DAILY:
            if now.hour == self.hour_of_day and now.minute < 5:
                # 避免重复触发
                if self._last_scheduled_run != now.strftime("%Y-%m-%d"):
                    should_trigger = True
                    reason = f"每日巡检 {self.hour_of_day}:00 UTC"
                    self._last_scheduled_run = now.strftime("%Y-%m-%d")

        elif self.schedule_type == ScheduleType.WEEKLY:
            if now.weekday() + 1 == self.day_of_week and now.hour == self.hour_of_day and now.minute < 5:
                if self._last_scheduled_run != now.strftime("%Y-W%W"):
                    should_trigger = True
                    reason = f"每周巡检 周{self.day_of_week} {self.hour_of_day}:00 UTC"
                    self._last_scheduled_run = now.strftime("%Y-W%W")

        if should_trigger:
            return self._record_trigger(TriggerResult(
                triggered=True,
                trigger_type=self.trigger_type,
                trigger_reason=reason,
                severity=1,  # 定时触发通常是低优先级巡检
                context={
                    "schedule_type": self.schedule_type.name,
                    "current_utc": now.isoformat(),
                    "hour": now.hour,
                    "weekday": now.weekday() + 1,
                },
                suggested_action="LOW: 定时巡检，生成健康报告，检查所有触发器状态和趋势",
            ))

        return self._not_triggered(f"not at scheduled time ({self.schedule_type.name})")


# ─────────────────────────────────────────────
# P1: 漂移触发器
# ─────────────────────────────────────────────

@dataclass
class BaselineMetrics:
    """基线指标（静态快照）"""
    avg_response_time: float = 0.0
    success_rate: float = 1.0
    avg_token_usage: float = 0.0
    top_behaviors: list[str] = field(default_factory=list)
    captured_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class DriftTrigger(BaseTrigger):
    """
    漂移检测触发器

    触发条件：当前行为/性能偏离基线超过 threshold
    """

    def __init__(
        self,
        response_time_drift_threshold: float = 0.3,   # 响应时间漂移 30%+
        success_rate_drift_threshold: float = 0.15,   # 成功率下降 15%+
        token_usage_drift_threshold: float = 0.4,     # Token使用漂移 40%+
        enabled: bool = True,
        priority: int = 2,
    ):
        super().__init__(
            name="DriftTrigger",
            trigger_type=TriggerType.DRIFT,
            enabled=enabled,
            priority=priority,
        )
        self.response_time_drift_threshold = response_time_drift_threshold
        self.success_rate_drift_threshold = success_rate_drift_threshold
        self.token_usage_drift_threshold = token_usage_drift_threshold
        self._baseline: Optional[BaselineMetrics] = None
        self._current_metrics: dict[str, float] = {}

    def capture_baseline(
        self,
        avg_response_time: float,
        success_rate: float,
        avg_token_usage: float,
        top_behaviors: Optional[list[str]] = None,
    ):
        """手动设置基线（通常在系统稳定时调用）"""
        self._baseline = BaselineMetrics(
            avg_response_time=avg_response_time,
            success_rate=success_rate,
            avg_token_usage=avg_token_usage,
            top_behaviors=top_behaviors or [],
            captured_at=datetime.utcnow().isoformat(),
        )

    def record_current(
        self,
        avg_response_time: float,
        success_rate: float,
        avg_token_usage: float,
    ):
        self._current_metrics = {
            "avg_response_time": avg_response_time,
            "success_rate": success_rate,
            "avg_token_usage": avg_token_usage,
        }

    def check(self) -> TriggerResult:
        if not self.enabled:
            return self._not_triggered("trigger disabled")
        if self._is_in_cooldown():
            return self._not_triggered("in cooldown period")
        if self._baseline is None:
            return self._not_triggered("no baseline captured yet")

        reasons = []
        severity = 1

        # 响应时间漂移
        curr_rt = self._current_metrics.get("avg_response_time", 0)
        base_rt = self._baseline.avg_response_time
        if base_rt > 0:
            rt_drift = (curr_rt - base_rt) / base_rt
            if rt_drift > self.response_time_drift_threshold:
                reasons.append(f"响应时间漂移 {rt_drift:+.0%} (>{self.response_time_drift_threshold:.0%})")
                severity = max(severity, 3)

        # 成功率漂移
        curr_sr = self._current_metrics.get("success_rate", 1.0)
        base_sr = self._baseline.success_rate
        if curr_sr < base_sr - self.success_rate_drift_threshold:
            drift = base_sr - curr_sr
            reasons.append(f"成功率下降 {drift:.0%} (>{self.success_rate_drift_threshold:.0%})")
            severity = max(severity, 4)

        # Token使用漂移
        curr_tu = self._current_metrics.get("avg_token_usage", 0)
        base_tu = self._baseline.avg_token_usage
        if base_tu > 0:
            tu_drift = (curr_tu - base_tu) / base_tu
            if abs(tu_drift) > self.token_usage_drift_threshold:
                reasons.append(f"Token使用漂移 {tu_drift:+.0%} (阈值 {self.token_usage_drift_threshold:.0%})")
                severity = max(severity, 2)

        if reasons:
            return self._record_trigger(TriggerResult(
                triggered=True,
                trigger_type=self.trigger_type,
                trigger_reason="; ".join(reasons),
                severity=severity,
                context={
                    "baseline": asdict(self._baseline),
                    "current": self._current_metrics,
                    "baseline_captured_at": self._baseline.captured_at,
                },
                suggested_action=self._suggest_action(severity),
            ))

        return self._not_triggered("metrics within baseline tolerance")

    def _suggest_action(self, severity: int) -> str:
        if severity >= 4:
            return "HIGH: 成功率严重漂移，创建检查点，分析近期变更，识别根因"
        return "MEDIUM: 检测到行为漂移，建议记录当前快照与基线对比，执行诊断"


# ─────────────────────────────────────────────
# 触发器管理器
# ─────────────────────────────────────────────

class TriggerManager:
    """统一管理所有触发器"""

    def __init__(self):
        self._triggers: list[BaseTrigger] = []

    def register(self, trigger: BaseTrigger):
        self._triggers.append(trigger)

    def check_all(self) -> list[TriggerResult]:
        """检查所有触发器，按优先级排序"""
        results = [t.check() for t in self._triggers if t.enabled]
        return [r for r in results if r.triggered]

    def get_trigger(self, name: str) -> Optional[BaseTrigger]:
        for t in self._triggers:
            if t.name == name:
                return t
        return None
