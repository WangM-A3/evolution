"""
M-A3 Self-Evolution System
==========================
自进化系统核心包，按优先级实现：
  P0: 触发器系统 (triggers.py) + 验证闭环 (verification.py)
  P1: 模式聚合算法 (pattern_aggregator.py)
  P2: GEP进化协议层完整实现 (protocol/, engine.py)

目录结构：
  evolution/
  ├── __init__.py          # 本文件
  ├── triggers.py          # 六类自动触发器
  ├── pattern_aggregator.py # 模式聚合算法
  ├── verification.py      # 验证闭环
  ├── engine.py             # 进化引擎
  ├── config.py             # 配置加载器
  ├── config.yaml           # YAML配置文件
  ├── genes.json            # 基因定义（可进化参数）
  ├── capsules.json         # 能力胶囊（封装技能）
  ├── events.jsonl          # 进化事件日志
  ├── checkpoints/           # 检查点存储
  ├── rules/                # 生成的规则
  ├── failures/              # 失败记录
  └── capsules/              # 能力胶囊存储
"""

from evolution.triggers import (
    PerformanceTrigger,
    FailureTrigger,
    PatternTrigger,
    FeedbackTrigger,
    ScheduleTrigger,
    DriftTrigger,
    TriggerResult,
    TriggerType,
    TriggerManager,
    BaseTrigger,
)
from evolution.pattern_aggregator import PatternAggregator, Pattern
from evolution.verification import VerificationLoop, Checkpoint, VerificationResult
from evolution.engine import EvolutionEngine, EvolutionEvent, EvolutionCycle, BusinessDrivenEvolution
from evolution.sharing import CapabilitySharing, CapabilityExport, CapabilityRating
from evolution.collaborative import CollaborativeEvolution, AgentMember, TeamDecision, SharedEvolutionState
from evolution.config import EvolutionConfig

from evolution.verification_report import (
    VerificationReport,
    CheckResult,
    CheckCategory,
    ReportStatus,
)
from evolution.verifier import (
    IndependentVerifier,
    Criterion,
    CriterionType,
    CriterionSeverity,
    Validators,
)
from evolution.stop_hook import (
    StopHook,
    HookMode,
    HookEvent,
    HookAttempt,
    HookResult,
)
from evolution.sprint_contract import (
    SprintContract,
    ContractExecutor,
    ContractStatus,
    Deliverable,
    AcceptanceCriterion,
    ContractParty,
    DeliverablePriority,
)
from evolution.three_agent_topology import (
    ThreeAgentTopology,
    PlannerAgent,
    GeneratorAgent,
    EvaluatorAgent,
    TopologySession,
    TaskStep,
    ExecutionRecord,
    AgentRole,
    StepStatus,
    SessionStatus,
)
from evolution.simplification_audit import (
    SimplificationAuditor,
    SimplificationReport,
    AuditResult,
    ComponentMetadata,
    SimplifyAction,
    ComponentType,
)

__all__ = [
    # Triggers
    "PerformanceTrigger",
    "FailureTrigger",
    "PatternTrigger",
    "FeedbackTrigger",
    "ScheduleTrigger",
    "DriftTrigger",
    "TriggerResult",
    "TriggerType",
    "TriggerManager",
    "BaseTrigger",
    # Aggregator
    "PatternAggregator",
    "Pattern",
    # Verification
    "VerificationLoop",
    "Checkpoint",
    "VerificationResult",
    # Engine
    "EvolutionEngine",
    "EvolutionEvent",
    "EvolutionCycle",
    # Business-Driven Evolution (NEW)
    "BusinessDrivenEvolution",
    # Capability Sharing (NEW)
    "CapabilitySharing",
    "CapabilityExport",
    "CapabilityRating",
    # Collaborative Evolution (NEW)
    "CollaborativeEvolution",
    "AgentMember",
    "TeamDecision",
    "SharedEvolutionState",
    # Config
    "EvolutionConfig",
    # Verification Report
    "VerificationReport",
    "CheckResult",
    "CheckCategory",
    "ReportStatus",
    # Independent Verifier
    "IndependentVerifier",
    "Criterion",
    "CriterionType",
    "CriterionSeverity",
    "Validators",
    # Stop Hook
    "StopHook",
    "HookMode",
    "HookEvent",
    "HookAttempt",
    "HookResult",
    # Sprint Contract
    "SprintContract",
    "ContractExecutor",
    "ContractStatus",
    "Deliverable",
    "AcceptanceCriterion",
    "ContractParty",
    "DeliverablePriority",
    # Three-Agent Topology
    "ThreeAgentTopology",
    "PlannerAgent",
    "GeneratorAgent",
    "EvaluatorAgent",
    "TopologySession",
    "TaskStep",
    "ExecutionRecord",
    "AgentRole",
    "StepStatus",
    "SessionStatus",
    # Simplification Audit
    "SimplificationAuditor",
    "SimplificationReport",
    "AuditResult",
    "ComponentMetadata",
    "SimplifyAction",
    "ComponentType",
]
