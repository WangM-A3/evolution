"""
evolution/engine.py
====================
进化引擎（Evolution Engine）

核心职责：协调所有子系统，执行完整的进化周期

进化周期（Evolution Cycle）：
  1. check_triggers()        - 检查所有触发器，收集触发结果
  2. aggregate_patterns()    - 聚合触发结果中的模式
  3. propose_changes()       - 基于模式生成变更建议
  4. verify_and_apply()      - 验证并应用变更（通过验证闭环）
  5. log_evolution_event()   - 记录进化事件到 events.jsonl

集成接口：
  - 读取 MEMORY.md（工作记忆）
  - 读取 SOUL.md（M-A3 身份）
  - 写入 rules/（生成的规则）
  - 写入 failures/（失败记录）
  - 写入 learnings/（学习文档）
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import Optional, Any
import logging

from evolution.triggers import (
    BaseTrigger,
    TriggerResult,
    TriggerManager,
    PerformanceTrigger,
    FailureTrigger,
    PatternTrigger,
    FeedbackTrigger,
    ScheduleTrigger,
    DriftTrigger,
    ScheduleType,
)
from evolution.pattern_aggregator import PatternAggregator, Pattern, RuleCandidate
from evolution.verification import (
    VerificationLoop,
    Checkpoint,
    VerificationResult,
    ProposedChange,
    ChangeType,
    VerificationStatus,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 枚举与数据结构
# ─────────────────────────────────────────────

class EvolutionPhase(Enum):
    IDLE = auto()
    TRIGGER_CHECK = auto()
    PATTERN_AGGREGATION = auto()
    CHANGE_PROPOSAL = auto()
    VERIFICATION = auto()
    APPLICATION = auto()
    COMPLETED = auto()
    ROLLED_BACK = auto()
    FAILED = auto()


class EvolutionSeverity(Enum):
    INFO = 1
    LOW = 2
    MEDIUM = 3
    HIGH = 4
    CRITICAL = 5


@dataclass
class EvolutionEvent:
    """进化事件（写入 events.jsonl）"""
    event_id: str
    timestamp: str
    phase: str
    trigger_type: Optional[str]
    trigger_reason: Optional[str]
    severity: int
    pattern_id: Optional[str]
    change_id: Optional[str]
    verification_status: Optional[str]
    rollback_performed: bool
    promoted: bool
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "phase": self.phase if isinstance(self.phase, str) else self.phase.name,
            "trigger_type": self.trigger_type,
            "trigger_reason": self.trigger_reason,
            "severity": self.severity,
            "pattern_id": self.pattern_id,
            "change_id": self.change_id,
            "verification_status": self.verification_status,
            "rollback_performed": self.rollback_performed,
            "promoted": self.promoted,
            "metadata": self.metadata,
        }

    def to_jsonl(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass
class EvolutionCycle:
    """进化周期记录"""
    cycle_id: str
    started_at: str
    ended_at: Optional[str] = None
    phase: EvolutionPhase = EvolutionPhase.IDLE
    triggers_fired: list[dict] = field(default_factory=list)
    patterns_found: list[str] = field(default_factory=list)    # pattern_ids
    changes_proposed: int = 0
    changes_applied: int = 0
    changes_rolled_back: int = 0
    events_logged: list[str] = field(default_factory=list)       # event_ids
    final_status: str = "running"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["phase"] = self.phase.name
        return d


# ─────────────────────────────────────────────
# 进化引擎
# ─────────────────────────────────────────────

class EvolutionEngine:
    """
    进化引擎

    使用示例：
        engine = EvolutionEngine(config_path="evolution/config.yaml")
        engine.init_triggers()
        result = engine.run_evolution_cycle()
    """

    def __init__(
        self,
        checkpoints_dir: str = "evolution/checkpoints",
        rules_dir: str = "evolution/rules",
        failures_dir: str = "evolution/failures",
        events_file: str = "evolution/events.jsonl",
        learnings_dir: str = "learnings",
        soul_path: str = "基础设定/SOUL.md",
        memory_path: str = "MEMORY.md",
    ):
        self.checkpoints_dir = Path(checkpoints_dir)
        self.rules_dir = Path(rules_dir)
        self.failures_dir = Path(failures_dir)
        self.events_file = Path(events_file)
        self.learnings_dir = Path(learnings_dir)
        self.soul_path = Path(soul_path)
        self.memory_path = Path(memory_path)

        # 初始化目录
        for d in [self.checkpoints_dir, self.rules_dir, self.failures_dir, self.learnings_dir]:
            d.mkdir(parents=True, exist_ok=True)

        # 初始化子系统
        self.trigger_manager = TriggerManager()
        self.aggregator = PatternAggregator(
            rules_dir=str(self.rules_dir),
            failures_dir=str(self.failures_dir),
        )
        self.verification_loop = VerificationLoop(
            checkpoints_dir=str(self.checkpoints_dir),
            failures_dir=str(self.failures_dir),
            rules_dir=str(self.rules_dir),
        )

        # 当前周期状态
        self._current_cycle: Optional[EvolutionCycle] = None
        self._cycle_counter = 0

        # 事件文件初始化
        if not self.events_file.exists():
            self.events_file.write_text("", encoding="utf-8")

    # ── 初始化 ──

    def init_triggers(
        self,
        config: Optional[dict[str, Any]] = None,
    ):
        """初始化所有触发器（从配置或默认值）"""
        cfg = config or {}

        perf_cfg = cfg.get("performance_trigger", {})
        self.trigger_manager.register(PerformanceTrigger(
            threshold_p95=perf_cfg.get("threshold_p95", 5.0),
            threshold_avg10=perf_cfg.get("threshold_avg10", 3.0),
            threshold_single=perf_cfg.get("threshold_single", 10.0),
            window_size=perf_cfg.get("window_size", 100),
        ))

        fail_cfg = cfg.get("failure_trigger", {})
        self.trigger_manager.register(FailureTrigger(
            consecutive_threshold=fail_cfg.get("consecutive_threshold", 3),
            total_threshold=fail_cfg.get("total_threshold", 20),
        ))

        pat_cfg = cfg.get("pattern_trigger", {})
        self.trigger_manager.register(PatternTrigger(
            min_occurrence=pat_cfg.get("min_occurrence", 3),
        ))

        fb_cfg = cfg.get("feedback_trigger", {})
        self.trigger_manager.register(FeedbackTrigger(
            threshold_negative=fb_cfg.get("threshold_negative", 5),
            threshold_sequence=fb_cfg.get("threshold_sequence", 3),
        ))

        self.trigger_manager.register(ScheduleTrigger(
            schedule_type=ScheduleType.DAILY,
            hour_of_day=cfg.get("schedule_hour", 9),
        ))

        drift_cfg = cfg.get("drift_trigger", {})
        self.trigger_manager.register(DriftTrigger(
            response_time_drift_threshold=drift_cfg.get("response_time_drift_threshold", 0.3),
            success_rate_drift_threshold=drift_cfg.get("success_rate_drift_threshold", 0.15),
        ))

        logger.info(f"Initialized {len(self.trigger_manager._triggers)} triggers")

    # ── 核心方法 ──

    def check_triggers(self) -> list[TriggerResult]:
        """
        检查所有触发器

        Returns:
            触发结果列表（按 severity 降序）
        """
        results = self.trigger_manager.check_all()
        results.sort(key=lambda r: r.severity, reverse=True)
        logger.info(f"Trigger check: {len(results)} fired out of {len(self.trigger_manager._triggers)} triggers")
        return results

    def run_evolution_cycle(
        self,
        triggered_results: Optional[list[TriggerResult]] = None,
        force_run: bool = False,
    ) -> EvolutionCycle:
        """
        执行完整的进化周期

        Args:
            triggered_results: 外部传入的触发结果（可选，默认内部检查）
            force_run: 是否强制运行（即使无触发器触发）

        Returns:
            EvolutionCycle 记录
        """
        self._cycle_counter += 1
        cycle_id = f"EC-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{self._cycle_counter:03d}"
        self._current_cycle = EvolutionCycle(
            cycle_id=cycle_id,
            started_at=datetime.utcnow().isoformat(),
            phase=EvolutionPhase.TRIGGER_CHECK,
        )

        # Step 1: 触发器检查
        if triggered_results is None:
            triggered_results = self.check_triggers()

        self._current_cycle.triggers_fired = [r.to_dict() for r in triggered_results]

        if not triggered_results and not force_run:
            self._current_cycle.phase = EvolutionPhase.COMPLETED
            self._current_cycle.final_status = "no_triggers"
            self._log_phase_event("NO_TRIGGERS", None, triggered_results)
            return self._current_cycle

        self._current_cycle.phase = EvolutionPhase.PATTERN_AGGREGATION

        # Step 2: 模式聚合
        for result in triggered_results:
            self.aggregator.add_from_trigger_result(result.to_dict())

        patterns = self.aggregator.extract_common_patterns()
        self._current_cycle.patterns_found = [p.pattern_id for p in patterns]
        logger.info(f"Pattern aggregation: {len(patterns)} patterns found")

        self._current_cycle.phase = EvolutionPhase.CHANGE_PROPOSAL

        # Step 3: 生成变更建议
        candidates = self.aggregator.generate_rule_candidates(patterns)
        self._current_cycle.changes_proposed = len(candidates)
        logger.info(f"Change proposal: {len(candidates)} candidates generated")

        # 保存候选
        if candidates:
            self.aggregator.save_candidates(candidates)
            self._log_phase_event("CANDIDATES_GENERATED", candidates[0], triggered_results)

        self._current_cycle.phase = EvolutionPhase.VERIFICATION

        # Step 4: 验证并应用（对高优先级候选执行）
        applied = 0
        rolled_back = 0
        for candidate in candidates[:3]:  # 最多处理前3个
            if candidate.priority > 2:  # 跳过低优先级
                continue

            success = self._verify_and_apply_candidate(candidate, patterns)
            if success:
                applied += 1
            else:
                rolled_back += 1

        self._current_cycle.changes_applied = applied
        self._current_cycle.changes_rolled_back = rolled_back
        self._current_cycle.phase = EvolutionPhase.COMPLETED
        self._current_cycle.ended_at = datetime.utcnow().isoformat()
        self._current_cycle.final_status = (
            "success" if rolled_back == 0 else f"partial({rolled_back} rolled back)"
        )

        # 保存patterns
        if patterns:
            self.aggregator.save_patterns(patterns)

        # 记录周期结束事件
        self._log_cycle_event(self._current_cycle)

        return self._current_cycle

    def _verify_and_apply_candidate(
        self,
        candidate: RuleCandidate,
        patterns: list[Pattern],
    ) -> bool:
        """验证并应用单个候选"""
        pattern = next((p for p in patterns if p.pattern_id == candidate.pattern_id), None)
        if pattern is None:
            return False

        # 创建检查点（只快照关键配置文件，不快照 rules 目录本身）
        checkpoint_files = [
            "evolution/genes.json",
            "evolution/capsules.json",
            "evolution/config.yaml",
            str(self.rules_dir / f"{candidate.pattern_id}.md"),
        ]
        cp = self.verification_loop.create_checkpoint(
            description=f"Before applying rule: {candidate.pattern_id}",
            tracked_files=checkpoint_files,
            metrics={"pattern_importance": pattern.importance_score},
            change_description=f"New rule from pattern {candidate.pattern_id}",
        )

        # 构建变更
        change = ProposedChange(
            change_id=candidate.pattern_id,
            change_type=ChangeType.RULE_ADD,
            description=candidate.rule_text,
            target_files=[str(self.rules_dir / f"{candidate.pattern_id}.md")],
            proposed_content={
                str(self.rules_dir / f"{candidate.pattern_id}.md"): candidate.rule_text
            },
            rollback_from_checkpoint=cp.checkpoint_id,
            risk_level="medium",
        )

        # 应用变更
        applied, msg = self.verification_loop.apply_change(change)
        if not applied:
            logger.warning(f"Failed to apply change: {msg}")
            return False

        # 验证变更
        result = self.verification_loop.validate_change(change, cp)

        if result.status == VerificationStatus.PASSED:
            # 晋升
            self.verification_loop.promote_if_passed(result, candidate.rule_text)
            self._log_phase_event("PROMOTED", candidate, None)
            return True

        elif result.status in (VerificationStatus.FAILED, VerificationStatus.PARTIAL):
            # 回滚
            reason = result.error_message or "Verification failed"
            self.verification_loop.rollback_if_failed(result, change, reason)
            self._log_phase_event("ROLLED_BACK", candidate, None, reason)
            return False

        return False

    def aggregate_patterns(
        self,
        issues: Optional[list[dict]] = None,
    ) -> list[Pattern]:
        """手动调用模式聚合（从外部传入问题列表）"""
        if issues:
            for issue in issues:
                self.aggregator.add_issue(
                    text=issue.get("text", ""),
                    fingerprint=issue.get("fingerprint"),
                    context=issue.get("context", {}),
                    source=issue.get("source", "manual"),
                    severity=issue.get("severity", 1),
                )
        return self.aggregator.extract_common_patterns()

    def propose_changes(self, patterns: list[Pattern]) -> list[RuleCandidate]:
        """从模式生成变更建议"""
        return self.aggregator.generate_rule_candidates(patterns)

    def log_evolution_event(
        self,
        phase: EvolutionPhase,
        trigger_result: Optional[TriggerResult] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> EvolutionEvent:
        """记录进化事件到 events.jsonl"""
        event = EvolutionEvent(
            event_id=self._generate_event_id(),
            timestamp=datetime.utcnow().isoformat(),
            phase=phase.name,
            trigger_type=trigger_result.trigger_type.name if trigger_result else None,
            trigger_reason=trigger_result.trigger_reason if trigger_result else None,
            severity=trigger_result.severity if trigger_result else 0,
            pattern_id=metadata.get("pattern_id") if metadata else None,
            change_id=metadata.get("change_id") if metadata else None,
            verification_status=metadata.get("verification_status") if metadata else None,
            rollback_performed=metadata.get("rollback", False),
            promoted=metadata.get("promoted", False),
            metadata=metadata or {},
        )

        with open(self.events_file, "a", encoding="utf-8") as f:
            f.write(event.to_jsonl() + "\n")

        return event

    # ── 工具方法 ──

    def _log_phase_event(
        self,
        phase_name: str,
        candidate: Optional[RuleCandidate],
        triggered_results: Optional[list[TriggerResult]],
        extra_reason: str = "",
    ):
        """记录阶段性事件"""
        metadata: dict[str, Any] = {}
        if candidate:
            metadata["change_id"] = candidate.pattern_id
            metadata["priority"] = candidate.priority
        if triggered_results:
            metadata["triggers"] = [r.trigger_reason for r in triggered_results]
        if extra_reason:
            metadata["reason"] = extra_reason

        event = EvolutionEvent(
            event_id=self._generate_event_id(),
            timestamp=datetime.utcnow().isoformat(),
            phase=phase_name,
            trigger_type=None,
            trigger_reason=None,
            severity=0,
            pattern_id=candidate.pattern_id if candidate else None,
            change_id=candidate.pattern_id if candidate else None,
            verification_status=None,
            rollback_performed=phase_name == "ROLLED_BACK",
            promoted=phase_name == "PROMOTED",
            metadata=metadata,
        )

        with open(self.events_file, "a", encoding="utf-8") as f:
            f.write(event.to_jsonl() + "\n")

        if self._current_cycle:
            self._current_cycle.events_logged.append(event.event_id)

    def _log_cycle_event(self, cycle: EvolutionCycle):
        """记录周期结束事件"""
        event = EvolutionEvent(
            event_id=self._generate_event_id(),
            timestamp=datetime.utcnow().isoformat(),
            phase="CYCLE_COMPLETED",
            trigger_type=None,
            trigger_reason=None,
            severity=0,
            pattern_id=None,
            change_id=None,
            verification_status=cycle.final_status,
            rollback_performed=cycle.changes_rolled_back > 0,
            promoted=cycle.changes_applied > 0,
            metadata=cycle.to_dict(),
        )

        with open(self.events_file, "a", encoding="utf-8") as f:
            f.write(event.to_jsonl() + "\n")

    def _generate_event_id(self) -> str:
        ts = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
        return f"EVT-{ts}"

    # ── 外部接口 ──

    def record_response_time(self, response_time: float):
        """记录响应时间（供 PerformanceTrigger 使用）"""
        trigger = self.trigger_manager.get_trigger("PerformanceTrigger")
        if isinstance(trigger, PerformanceTrigger):
            trigger.record_response_time(response_time)

    def record_failure(
        self,
        failure_type: str,
        error_msg: str = "",
        context: Optional[dict] = None,
    ):
        """记录失败（供 FailureTrigger 使用）"""
        trigger = self.trigger_manager.get_trigger("FailureTrigger")
        if isinstance(trigger, FailureTrigger):
            trigger.record_failure(failure_type, error_msg, context)

    def record_issue(self, issue_text: str, context: Optional[dict] = None):
        """记录问题（供 PatternTrigger 使用）"""
        trigger = self.trigger_manager.get_trigger("PatternTrigger")
        if isinstance(trigger, PatternTrigger):
            trigger.record_issue(issue_text, context)

    def record_feedback(self, is_negative: bool, feedback_text: str = "", context: Optional[dict] = None):
        """记录反馈（供 FeedbackTrigger 使用）"""
        trigger = self.trigger_manager.get_trigger("FeedbackTrigger")
        if isinstance(trigger, FeedbackTrigger):
            trigger.record_feedback(is_negative, feedback_text, context)

    def capture_baseline(
        self,
        avg_response_time: float,
        success_rate: float,
        avg_token_usage: float,
        top_behaviors: Optional[list[str]] = None,
    ):
        """捕获行为基线（供 DriftTrigger 使用）"""
        trigger = self.trigger_manager.get_trigger("DriftTrigger")
        if isinstance(trigger, DriftTrigger):
            trigger.capture_baseline(avg_response_time, success_rate, avg_token_usage, top_behaviors)

    def get_current_cycle(self) -> Optional[EvolutionCycle]:
        return self._current_cycle

    def get_events(self, limit: int = 100) -> list[dict]:
        """读取最近的进化事件"""
        if not self.events_file.exists():
            return []
        lines = self.events_file.read_text(encoding="utf-8").strip().splitlines()
        events = []
        for line in lines[-limit:]:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return events

    def get_cycle_summary(self) -> dict:
        """获取进化周期摘要"""
        events = self.get_events(limit=1000)
        cycles = [e for e in events if e.get("phase") == "CYCLE_COMPLETED"]
        rollbacks = [e for e in events if e.get("rollback_performed")]
        promotions = [e for e in events if e.get("promoted")]

        return {
            "total_cycles": len(cycles),
            "total_events": len(events),
            "total_rollbacks": len(rollbacks),
            "total_promotions": len(promotions),
            "recent_cycles": cycles[-5:] if cycles else [],
        }


# ─────────────────────────────────────────────────────────────────────────────
# 业务驱动进化（Business-Driven Evolution）
# 参考 Accio Work：根据生意进展迭代商业判断能力
# ─────────────────────────────────────────────────────────────────────────────

class BusinessDrivenEvolution:
    """
    业务驱动进化模块

    核心职责：
    - 追踪业务指标（任务完成率、用户满意度、决策质量）
    - 评估每次决策的实际效果
    - 动态调整进化策略参数
    - 晋升表现优异的模式

    进化方向：
      track_business_metrics()     → 记录业务指标时间序列
      evaluate_decision_quality()  → 评估决策质量
      adjust_strategy()            → 基于效果调整基因参数
      promote_successful_patterns()→ 晋升高效模式
    """

    def __init__(
        self,
        engine: EvolutionEngine,
        metrics_file: str = "evolution/business_metrics.jsonl",
    ):
        self.engine = engine
        self.metrics_file = Path(metrics_file)
        self.metrics_file.parent.mkdir(parents=True, exist_ok=True)

        # 业务指标滑动窗口（避免单点波动影响）
        self._window_size = 50
        self._metrics_window: list[dict] = []
        self._decision_history: list[dict] = []

        # 策略基因映射：指标类型 → 受影响的基因名
        self._gene_map: dict[str, str] = {
            "task_completion_rate": "feedback_weight_gene",
            "avg_response_time": "response_threshold_gene",
            "failure_rate": "failure_sensitivity_gene",
            "pattern_recognition_rate": "pattern_recognition_gene",
            "decision_success_rate": "drift_tolerance_gene",
        }

        # 指标阈值（触发基因调整）
        self._thresholds: dict[str, dict] = {
            "task_completion_rate": {"low": 0.6, "high": 0.9},
            "avg_response_time": {"low": 1.0, "high": 8.0},
            "failure_rate": {"low": 0.02, "high": 0.2},
            "pattern_recognition_rate": {"low": 0.3, "high": 0.8},
            "decision_success_rate": {"low": 0.5, "high": 0.85},
        }

        # 加载已有指标窗口
        self._load_metrics_window()

    # ── 核心方法 ──────────────────────────────────────────────────────────────

    def track_business_metrics(self, snapshot: dict) -> dict:
        """
        记录业务指标快照并写入 metrics_file

        Args:
            snapshot: {
                "task_completion_rate": 0.85,   # 任务完成率
                "avg_response_time": 3.2,        # 平均响应时间（秒）
                "failure_rate": 0.05,             # 失败率
                "pattern_recognition_rate": 0.6,  # 模式识别命中率
                "decision_success_rate": 0.72,    # 决策成功率（外部用户标记）
                "user_satisfaction": 4.2,        # 用户满意度（1-5）
                "evolution_cycle_count": 12,      # 累计进化周期数
            }

        Returns:
            评估结果含当前窗口指标和调整建议
        """
        ts = datetime.utcnow().isoformat()
        record = {"timestamp": ts, **snapshot}

        # 写入文件
        with open(self.metrics_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        # 更新滑动窗口
        self._metrics_window.append(record)
        if len(self._metrics_window) > self._window_size:
            self._metrics_window.pop(0)

        # 评估调整需求
        adjustment = self._evaluate_adjustment_needed()
        self._log_business_event("METRICS_TRACKED", record, adjustment)

        return {
            "record": record,
            "window_avg": self._compute_window_avg(),
            "adjustment_needed": adjustment,
            "window_size": len(self._metrics_window),
        }

    def evaluate_decision_quality(
        self,
        decision_id: str,
        decision_context: dict,
        outcome: dict,
    ) -> dict:
        """
        评估单次决策质量，记录到决策历史

        Args:
            decision_id: 决策唯一标识
            decision_context: 决策时的上下文（触发器类型、输入、策略参数）
            outcome: {
                "success": bool,
                "task_completed": bool,
                "response_time": float,
                "error_type": Optional[str],
                "user_rating": Optional[float],   # 1-5
                "pattern_promoted": bool,
            }

        Returns:
            质量评分（0-1）和改进建议
        """
        # 多维度评分
        scores: dict[str, float] = {}

        # 任务完成得分
        scores["task_completion"] = 1.0 if outcome.get("task_completed") else 0.0

        # 响应时间得分（基于基因阈值）
        rt = outcome.get("response_time", 0)
        threshold = self._get_gene_value("response_threshold_gene")
        scores["response_quality"] = max(0.0, 1.0 - (rt / threshold)) if rt > 0 else 1.0

        # 用户满意度得分
        rating = outcome.get("user_rating")
        scores["satisfaction"] = (rating / 5.0) if rating else 0.5

        # 模式晋升得分
        scores["pattern_promotion"] = 1.0 if outcome.get("pattern_promoted") else 0.0

        # 综合质量分（加权平均）
        weights = {"task_completion": 0.4, "response_quality": 0.2,
                   "satisfaction": 0.2, "pattern_promotion": 0.2}
        quality_score = sum(scores[k] * weights[k] for k in weights)

        record = {
            "decision_id": decision_id,
            "timestamp": datetime.utcnow().isoformat(),
            "decision_context": decision_context,
            "outcome": outcome,
            "quality_score": quality_score,
            "dimension_scores": scores,
        }
        self._decision_history.append(record)

        # 记录到触发器（反馈进化）
        if quality_score < 0.4:
            self.engine.record_feedback(
                is_negative=True,
                feedback_text=f"Decision {decision_id} low quality: {quality_score:.2f}",
                context={"quality_score": quality_score, **decision_context},
            )
        else:
            self.engine.record_feedback(
                is_negative=False,
                feedback_text=f"Decision {decision_id} quality: {quality_score:.2f}",
                context={"quality_score": quality_score, **decision_context},
            )

        self._log_business_event("DECISION_EVALUATED", record, None)
        return record

    def adjust_strategy(self) -> dict:
        """
        基于业务指标窗口，动态调整基因参数

        Returns:
            调整结果：哪些基因被调整、调整幅度和方向
        """
        if len(self._metrics_window) < 5:
            return {"status": "insufficient_data", "adjustments": []}

        window_avg = self._compute_window_avg()
        results: list[dict] = []

        for metric_name, threshold in self._thresholds.items():
            value = window_avg.get(metric_name)
            if value is None:
                continue

            gene_name = self._gene_map.get(metric_name)
            if not gene_name:
                continue

            # 读取基因定义
            genes = self._load_genes()
            gene = genes.get("genes", {}).get(gene_name)
            if not gene or not gene.get("evolvable"):
                continue

            adjustment = self._compute_adjustment(
                metric_name, value, threshold, gene
            )
            if adjustment["changed"]:
                # 写回 genes.json
                self._mutate_gene(gene_name, adjustment["new_value"])
                results.append({
                    "gene": gene_name,
                    "metric": metric_name,
                    "old_value": gene["current_value"],
                    "new_value": adjustment["new_value"],
                    "delta": adjustment["delta"],
                    "reason": adjustment["reason"],
                    "window_avg": value,
                })
                self._log_business_event(
                    "GENE_ADJUSTED",
                    {"gene": gene_name, "adjustment": adjustment},
                    None,
                )

        return {"status": "adjusted", "adjustments": results, "window_avg": window_avg}

    def promote_successful_patterns(self) -> list[dict]:
        """
        从决策历史中识别持续成功的模式，晋升到能力胶囊

        Returns:
            被晋升的模式列表
        """
        if len(self._decision_history) < 5:
            return []

        # 分析最近N个决策中成功率≥80%的模式
        recent = self._decision_history[-20:]
        patterns_by_context: dict[str, list] = defaultdict(list)

        for decision in recent:
            ctx = decision.get("decision_context", {})
            trigger_type = ctx.get("trigger_type", "unknown")
            patterns_by_context[trigger_type].append(decision["quality_score"])

        # 计算每个触发类型的平均质量
        avg_by_trigger = {
            trigger: sum(scores) / len(scores)
            for trigger, scores in patterns_by_context.items()
        }

        promoted = []
        for trigger_type, avg_score in avg_by_trigger.items():
            if avg_score >= 0.75:
                # 晋升为能力胶囊
                capsule_id = f"CAP-{trigger_type.upper()}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
                capsule = {
                    "capsule_id": capsule_id,
                    "name": f"{trigger_type} 高效模式",
                    "description": (
                        f"基于 {trigger_type} 触发的决策历史，"
                        f"平均质量 {avg_score:.2f}，共 {len(patterns_by_context[trigger_type])} 条记录"
                    ),
                    "version": "1.0.0",
                    "capability_type": "pattern",
                    "trigger_conditions": [trigger_type],
                    "promotion_info": {
                        "avg_quality_score": avg_score,
                        "sample_size": len(patterns_by_context[trigger_type]),
                        "created_at": datetime.utcnow().isoformat(),
                        "verified": True,
                    },
                    "metadata": {
                        "author": "BusinessDrivenEvolution",
                        "stability": "stable",
                        "usage_count": 0,
                        "success_rate": avg_score,
                    },
                }
                self._save_capsule(capsule)
                promoted.append(capsule)
                self._log_business_event(
                    "PATTERN_PROMOTED",
                    {"capsule": capsule, "avg_score": avg_score},
                    None,
                )

        return promoted

    # ── 辅助方法 ──────────────────────────────────────────────────────────────

    def _load_metrics_window(self):
        """从文件加载最近的指标窗口"""
        if not self.metrics_file.exists():
            return
        lines = self.metrics_file.read_text(encoding="utf-8").strip().splitlines()
        for line in lines[-self._window_size:]:
            line = line.strip()
            if not line:
                continue
            try:
                self._metrics_window.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    def _compute_window_avg(self) -> dict:
        """计算滑动窗口内各指标均值"""
        if not self._metrics_window:
            return {}
        keys = ["task_completion_rate", "avg_response_time", "failure_rate",
                "pattern_recognition_rate", "decision_success_rate", "user_satisfaction"]
        result = {}
        for key in keys:
            values = [m.get(key) for m in self._metrics_window if m.get(key) is not None]
            if values:
                result[key] = sum(values) / len(values)
        return result

    def _evaluate_adjustment_needed(self) -> dict:
        """判断哪些指标需要调整"""
        if len(self._metrics_window) < 5:
            return {"needs_adjustment": False}
        avg = self._compute_window_avg()
        needs: list[dict] = []
        for metric, threshold in self._thresholds.items():
            val = avg.get(metric)
            if val is None:
                continue
            if val < threshold["low"]:
                needs.append({"metric": metric, "direction": "down", "value": val, "threshold": threshold})
            elif val > threshold["high"]:
                needs.append({"metric": metric, "direction": "up", "value": val, "threshold": threshold})
        return {"needs_adjustment": len(needs) > 0, "items": needs}

    def _compute_adjustment(
        self,
        metric_name: str,
        value: float,
        threshold: dict,
        gene: dict,
    ) -> dict:
        """计算单个基因的调整量"""
        current = gene["current_value"]
        min_v, max_v = gene["min_value"], gene["max_value"]
        mutation_rate = gene.get("mutation_rate", 0.1)

        # 根据偏离方向决定调整方向
        if value < threshold["low"]:
            # 指标偏低，需要基因做出补偿（调整方向取决于基因语义）
            direction = "increase" if metric_name in ("task_completion_rate", "pattern_recognition_rate", "decision_success_rate") else "decrease"
        elif value > threshold["high"]:
            direction = "decrease" if metric_name in ("task_completion_rate", "pattern_recognition_rate", "decision_success_rate") else "increase"
        else:
            return {"changed": False, "reason": "within_threshold"}

        # 限制调整幅度不超过 mutation_rate
        max_change = (max_v - min_v) * mutation_rate
        if direction == "increase":
            new_val = min(max_v, current + max_change)
        else:
            new_val = max(min_v, current - max_change)

        changed = abs(new_val - current) > 0.001 * (max_v - min_v)
        return {
            "changed": changed,
            "new_value": new_val,
            "delta": new_val - current,
            "reason": f"{direction} due to {metric_name}={value:.3f}",
        }

    def _get_gene_value(self, gene_name: str) -> float:
        genes = self._load_genes()
        gene = genes.get("genes", {}).get(gene_name, {})
        return gene.get("current_value", 1.0)

    def _load_genes(self) -> dict:
        genes_path = Path("evolution/genes.json")
        if genes_path.exists():
            return json.loads(genes_path.read_text(encoding="utf-8"))
        return {"genes": {}}

    def _mutate_gene(self, gene_name: str, new_value: float):
        genes = self._load_genes()
        if gene_name in genes.get("genes", {}):
            genes["genes"][gene_name]["current_value"] = new_value
            genes["last_updated"] = datetime.utcnow().isoformat()
            Path("evolution/genes.json").write_text(
                json.dumps(genes, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            logger.info(f"[BusinessDrivenEvolution] Gene mutated: {gene_name} = {new_value}")

    def _save_capsule(self, capsule: dict):
        capsules_path = Path("evolution/capsules.json")
        data = {"capsules": {}, "schema_version": "1.0", "last_updated": datetime.utcnow().isoformat()}
        if capsules_path.exists():
            try:
                data = json.loads(capsules_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        data["capsules"][capsule["capsule_id"]] = capsule
        data["last_updated"] = datetime.utcnow().isoformat()
        capsules_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"[BusinessDrivenEvolution] Capsule saved: {capsule['capsule_id']}")

    def _log_business_event(self, phase: str, data: dict, adjustment: Optional[dict]):
        """记录业务进化事件"""
        event = EvolutionEvent(
            event_id=f"BIZ-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}",
            timestamp=datetime.utcnow().isoformat(),
            phase=phase,
            trigger_type=None,
            trigger_reason=None,
            severity=0,
            pattern_id=None,
            change_id=None,
            verification_status=None,
            rollback_performed=False,
            promoted=phase == "PATTERN_PROMOTED",
            metadata={"business_data": data, "adjustment": adjustment} if adjustment else {"business_data": data},
        )
        with open(self.engine.events_file, "a", encoding="utf-8") as f:
            f.write(event.to_jsonl() + "\n")

    def get_business_summary(self) -> dict:
        """获取业务进化摘要"""
        return {
            "metrics_window_size": len(self._metrics_window),
            "window_avg": self._compute_window_avg(),
            "decisions_tracked": len(self._decision_history),
            "recent_decision_avg_quality": (
                sum(d["quality_score"] for d in self._decision_history[-20:]) / min(20, len(self._decision_history))
                if self._decision_history else None
            ),
            "adjustment_needed": self._evaluate_adjustment_needed(),
        }
