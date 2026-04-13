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
