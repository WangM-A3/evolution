"""
evolution/three_agent_topology.py
=================================
三Agent拓扑（Three-Agent Topology）

核心理念：分工制衡，防止单Agent自我闭环

三角色：
  Planner   - 规划器：分析需求、拆解任务、制定计划
  Generator - 生成器：执行具体工作（写代码、写文档）
  Evaluator - 评估器：独立验证、不依赖执行上下文、客观裁决

工作流：
  需求 → Planner分解 → Generator产出 → Evaluator验证
       ↑              ↑               ↓
       ←──反馈───────┘               │（失败）
                                     ↓
                                重新规划/产出

特殊设计：
  - Evaluator不接受Generator的直接输入，只读文件
  - Planner和Evaluator之间无直接通信（三角隔离）
  - Generator产出必须有对应的契约（Contract）

使用示例：
    topology = ThreeAgentTopology()

    # 启动一个完整任务
    session = topology.start_session(
        task_id="TASK-20260414-001",
        goal="实现用户登录",
    )

    # Planner: 分解任务
    plan = topology.plan(session.session_id, goal="实现用户登录")
    print(f"分解为{len(plan.steps)}步")

    # Generator: 逐步执行
    for step in plan.steps:
        result = topology.generate(session.session_id, step)

    # Evaluator: 验收
    verdict = topology.evaluate(session.session_id)
    print(verdict.summary())
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import Optional, Any


# ─────────────────────────────────────────────
# 枚举定义
# ─────────────────────────────────────────────

class AgentRole(Enum):
    PLANNER   = auto()   # 规划器
    GENERATOR = auto()   # 生成器
    EVALUATOR = auto()   # 评估器


class StepStatus(Enum):
    PENDING    = "pending"
    IN_PROGRESS = "in_progress"
    DONE       = "done"
    FAILED     = "failed"
    SKIPPED    = "skipped"


class SessionStatus(Enum):
    PLANNING  = "planning"
    GENERATING = "generating"
    EVALUATING = "evaluating"
    PASSED     = "passed"
    FAILED     = "failed"
    COMPLETED  = "completed"


# ─────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────

@dataclass
class TaskStep:
    """任务步骤"""
    step_id: str
    description: str
    role: AgentRole
    status: StepStatus = StepStatus.PENDING
    assigned_agent: str = ""
    inputs: list[str] = field(default_factory=list)     # 依赖的输入文件
    outputs: list[str] = field(default_factory=list)     # 产出的文件
    duration_ms: float = 0.0
    result: str = ""
    evaluation_note: str = ""
    created_at: str = ""
    started_at: Optional[str] = None
    ended_at: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["role"] = self.role.name
        d["status"] = self.status.value
        return d


@dataclass
class ExecutionRecord:
    """执行记录"""
    agent_id: str
    role: AgentRole
    action: str
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    duration_ms: float = 0.0
    success: bool = True
    error: str = ""
    timestamp: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["role"] = self.role.name
        return d


@dataclass
class TopologySession:
    """三Agent拓扑会话"""
    session_id: str
    task_id: str
    goal: str
    status: SessionStatus = SessionStatus.PLANNING
    plan: list[TaskStep] = field(default_factory=list)
    records: list[ExecutionRecord] = field(default_factory=list)
    created_at: str = ""
    last_updated: str = ""
    verdict: str = ""
    summary: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "task_id": self.task_id,
            "goal": self.goal,
            "status": self.status.value,
            "plan": [s.to_dict() for s in self.plan],
            "records": [r.to_dict() for r in self.records],
            "created_at": self.created_at,
            "last_updated": self.last_updated,
            "verdict": self.verdict,
            "summary": self.summary,
        }


# ─────────────────────────────────────────────
# Planner Agent（规划器）
# ─────────────────────────────────────────────

class PlannerAgent:
    """
    Planner Agent — 任务规划器

    职责：
      - 分析需求，拆解为可执行步骤
      - 确定每个步骤的输入/输出
      - 识别依赖关系（拓扑排序）
      - 分配角色（Plan/Gen/Eval）
    """

    def __init__(self, agent_id: str = "planner-001"):
        self.agent_id = agent_id

    def plan(
        self,
        task_id: str,
        goal: str,
        context: Optional[dict[str, Any]] = None,
    ) -> list[TaskStep]:
        """
        分析目标，拆解为任务步骤

        Returns:
            排序后的步骤列表
        """
        context = context or {}
        steps: list[TaskStep] = []
        ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")

        # 步骤1：分析需求（Planner）
        steps.append(TaskStep(
            step_id=f"STEP-{ts}-001",
            description=f"分析需求: {goal}",
            role=AgentRole.PLANNER,
            inputs=[],
            outputs=["evolution/sessions/{task_id}/analysis.md"],
            created_at=datetime.utcnow().isoformat(),
        ))

        # 步骤2：创建Sprint契约（Planner + Evaluator联动）
        steps.append(TaskStep(
            step_id=f"STEP-{ts}-002",
            description=f"创建Sprint契约",
            role=AgentRole.PLANNER,
            inputs=[],
            outputs=["evolution/contracts/{task_id}.json"],
            created_at=datetime.utcnow().isoformat(),
        ))

        # 步骤3：创建进度文件（Generator）
        steps.append(TaskStep(
            step_id=f"STEP-{ts}-003",
            description="创建进度追踪文件",
            role=AgentRole.GENERATOR,
            inputs=[],
            outputs=["m-a3-harness-progress.json"],
            created_at=datetime.utcnow().isoformat(),
        ))

        # 步骤4：实现功能（Generator）
        # 拆解为多个子功能（按context中的features）
        features = context.get("features", [])
        if features:
            for i, feature in enumerate(features, 1):
                steps.append(TaskStep(
                    step_id=f"STEP-{ts}-{100+i:03d}",
                    description=f"实现功能: {feature.get('name', f'功能{i}')}",
                    role=AgentRole.GENERATOR,
                    inputs=[f"m-a3-harness-progress.json"],
                    outputs=[feature.get("output", f"src/feature_{i}.py")],
                    created_at=datetime.utcnow().isoformat(),
                ))

        # 步骤5：StopHook验证（Evaluator）
        steps.append(TaskStep(
            step_id=f"STEP-{ts}-900",
            description="执行StopHook验证",
            role=AgentRole.EVALUATOR,
            inputs=[s.outputs[0] for s in steps if s.outputs],
            outputs=["evolution/reports/{task_id}-verification-report.json"],
            created_at=datetime.utcnow().isoformat(),
        ))

        # 步骤6：最终评估（Evaluator）
        steps.append(TaskStep(
            step_id=f"STEP-{ts}-999",
            description="最终评估并关闭契约",
            role=AgentRole.EVALUATOR,
            inputs=["evolution/reports/{task_id}-verification-report.json"],
            outputs=["evolution/sessions/{task_id}/final-report.json"],
            created_at=datetime.utcnow().isoformat(),
        ))

        return steps

    def analyze(self, goal: str) -> dict:
        """
        分析目标，输出分析报告

        返回：
          - scope: 范围界定
          - risks: 潜在风险
          - dependencies: 依赖项
          - milestones: 里程碑
        """
        return {
            "goal": goal,
            "scope": goal,
            "estimated_steps": 5,
            "estimated_duration_minutes": 30,
            "risks": ["上下文溢出风险", "验证覆盖率不足"],
            "dependencies": ["Python 3.x", "pytest"],
            "milestones": ["进度文件创建", "功能实现", "验证通过"],
        }


# ─────────────────────────────────────────────
# Generator Agent（生成器）
# ─────────────────────────────────────────────

class GeneratorAgent:
    """
    Generator Agent — 内容生成器

    职责：
      - 执行具体工作（写代码、写文档）
      - 产出文件到指定路径
      - 记录产出清单（供Evaluator使用）
      - 不进行自我验证

    注意：Generator只产出文件，不做判断
    """

    def __init__(self, agent_id: str = "generator-001"):
        self.agent_id = agent_id

    def generate(
        self,
        step: TaskStep,
        context: Optional[dict[str, Any]] = None,
    ) -> tuple[bool, str]:
        """
        执行单步生成

        Returns:
            (success, detail)
        """
        context = context or {}
        if step.role != AgentRole.GENERATOR:
            return False, f"Generator不能执行{step.role.name}角色的步骤"

        # 根据步骤描述模拟生成动作
        # 真实实现会调用LLM写代码
        detail = f"Generator已产出: {', '.join(step.outputs)}"
        return True, detail

    def create_progress_file(
        self,
        task_id: str,
        goal: str,
        features: list[dict],
    ) -> str:
        """创建进度追踪文件"""
        from m_a3_harness_progress import HarnessProgress

        progress = HarnessProgress.create(task_id, goal)
        for f in features:
            progress.add_feature(f.get("name", "未命名"), f.get("description", ""))
        path = progress.save()
        return path


# ─────────────────────────────────────────────
# Evaluator Agent（评估器）
# ─────────────────────────────────────────────

class EvaluatorAgent:
    """
    Evaluator Agent — 独立评估器

    核心原则：
      - 不接受Generator的口头报告，只读文件
      - 基于客观标准评判
      - 裁决不可协商

    职责：
      - 执行StopHook验证
      - 评估步骤完成质量
      - 生成最终裁决报告
    """

    def __init__(
        self,
        verifier=None,
        stop_hook=None,
        agent_id: str = "evaluator-001",
    ):
        self.agent_id = agent_id
        self.verifier = verifier
        self.stop_hook = stop_hook

    def evaluate_step(self, step: TaskStep) -> tuple[bool, str]:
        """
        评估单个步骤是否完成

        Returns:
            (passed, evaluation_note)
        """
        # 检查输出文件是否都存在
        missing = []
        for output in step.outputs:
            output_filled = output.format(task_id=step.step_id.split("-")[1] if "-" in step.step_id else step.step_id)
            if not Path(output_filled).exists():
                missing.append(output_filled)

        if not missing:
            return True, f"✓ 步骤{step.step_id}完成，{len(step.outputs)}个文件已产出"
        return False, f"✗ 步骤{step.step_id}缺少文件: {', '.join(missing)}"

    def evaluate_session(
        self,
        session: TopologySession,
    ) -> dict:
        """
        评估整个会话的完成情况

        Returns:
            评估结果字典
        """
        from evolution.verification_report import ReportStatus

        total = len(session.plan)
        passed = sum(1 for s in session.plan if s.status == StepStatus.DONE)
        failed = sum(1 for s in session.plan if s.status == StepStatus.FAILED)

        # Evaluator的裁决
        if failed > 0:
            verdict = f"❌ 评估失败 — {failed}/{total} 步骤失败"
            status = ReportStatus.FAILED
        elif passed == total:
            verdict = f"✅ 评估通过 — {passed}/{total} 步骤全部完成"
            status = ReportStatus.PASSED
        else:
            verdict = f"⚠️  部分完成 — {passed}/{total} 步骤"
            status = ReportStatus.PARTIAL

        return {
            "verdict": verdict,
            "status": status.value,
            "total_steps": total,
            "passed_steps": passed,
            "failed_steps": failed,
            "completion_rate": passed / total if total else 0,
        }


# ─────────────────────────────────────────────
# 三Agent拓扑管理器
# ─────────────────────────────────────────────

class ThreeAgentTopology:
    """
    三Agent拓扑管理器

    使用示例：
        topology = ThreeAgentTopology()

        # 启动会话
        session = topology.start_session("TASK-001", "实现登录")

        # Planner: 制定计划
        plan = topology.plan(session.session_id, goal="实现登录", features=[...])

        # Generator: 执行步骤
        for step in plan:
            topology.execute_step(step)

        # Evaluator: 最终裁决
        verdict = topology.evaluate(session.session_id)
    """

    def __init__(
        self,
        base_dir: str = "evolution/sessions",
        verifier=None,
        stop_hook=None,
    ):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.sessions: dict[str, TopologySession] = {}

        self.planner = PlannerAgent()
        self.generator = GeneratorAgent()
        self.evaluator = EvaluatorAgent(verifier=verifier, stop_hook=stop_hook)

    # ── 会话管理 ──────────────────────────────────────────────────────────────

    def start_session(
        self,
        task_id: str,
        goal: str,
    ) -> TopologySession:
        """启动一个新会话"""
        ts = datetime.utcnow()
        session_id = f"SESSION-{task_id}-{ts.strftime('%Y%m%d%H%M%S')}"

        session = TopologySession(
            session_id=session_id,
            task_id=task_id,
            goal=goal,
            status=SessionStatus.PLANNING,
            created_at=ts.isoformat(),
            last_updated=ts.isoformat(),
        )
        self.sessions[session_id] = session
        self._save_session(session)
        return session

    def get_session(self, session_id: str) -> Optional[TopologySession]:
        return self.sessions.get(session_id)

    # ── Plan阶段 ──────────────────────────────────────────────────────────────

    def plan(
        self,
        session_id: str,
        goal: str,
        features: Optional[list[dict]] = None,
    ) -> list[TaskStep]:
        """
        Planner: 制定任务计划
        """
        session = self.sessions.get(session_id)
        if not session:
            raise ValueError(f"Session不存在: {session_id}")

        session.status = SessionStatus.PLANNING
        steps = self.planner.plan(session.task_id, goal, context={"features": features or []})
        session.plan = steps
        session.last_updated = datetime.utcnow().isoformat()
        self._save_session(session)
        return steps

    # ── Generate阶段 ─────────────────────────────────────────────────────────

    def generate(
        self,
        session_id: str,
        step: TaskStep,
        context: Optional[dict[str, Any]] = None,
    ) -> tuple[bool, str]:
        """
        Generator: 执行单步生成
        """
        session = self.sessions.get(session_id)
        if not session:
            raise ValueError(f"Session不存在: {session_id}")

        session.status = SessionStatus.GENERATING
        step.status = StepStatus.IN_PROGRESS
        step.started_at = datetime.utcnow().isoformat()

        start_time = time.time()
        if step.role == AgentRole.GENERATOR:
            success, detail = self.generator.generate(step, context)
        else:
            success = True
            detail = f"[Generator跳过] 非Generator角色: {step.role.name}"

        step.duration_ms = (time.time() - start_time) * 1000
        step.status = StepStatus.DONE if success else StepStatus.FAILED
        step.result = detail
        step.ended_at = datetime.utcnow().isoformat()

        # 记录执行
        record = ExecutionRecord(
            agent_id=self.generator.agent_id,
            role=AgentRole.GENERATOR,
            action=f"generate:{step.step_id}",
            inputs=step.inputs,
            outputs=step.outputs,
            duration_ms=step.duration_ms,
            success=success,
            error="" if success else detail,
            timestamp=datetime.utcnow().isoformat(),
        )
        session.records.append(record)
        session.last_updated = datetime.utcnow().isoformat()
        self._save_session(session)
        return success, detail

    # ── Evaluate阶段 ─────────────────────────────────────────────────────────

    def evaluate(self, session_id: str) -> dict:
        """
        Evaluator: 评估整个会话
        """
        session = self.sessions.get(session_id)
        if not session:
            raise ValueError(f"Session不存在: {session_id}")

        session.status = SessionStatus.EVALUATING

        # 逐步骤评估
        for step in session.plan:
            if step.role == AgentRole.EVALUATOR:
                passed, note = self.evaluator.evaluate_step(step)
                step.evaluation_note = note
                step.status = StepStatus.DONE if passed else StepStatus.FAILED

        # 整体评估
        result = self.evaluator.evaluate_session(session)
        session.verdict = result["verdict"]
        session.status = SessionStatus.PASSED if result["status"] == "passed" else SessionStatus.FAILED
        session.last_updated = datetime.utcnow().isoformat()

        session.summary = {
            "plan": [s.to_dict() for s in session.plan],
            "evaluation": result,
            "records": [r.to_dict() for r in session.records],
        }

        self._save_session(session)
        return result

    # ── 辅助方法 ──────────────────────────────────────────────────────────────

    def _save_session(self, session: TopologySession):
        """保存会话到文件"""
        session_dir = self.base_dir / session.task_id
        session_dir.mkdir(parents=True, exist_ok=True)
        path = session_dir / f"{session.session_id}.json"
        path.write_text(json.dumps(session.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    def get_session_report(self, session_id: str) -> Optional[dict]:
        """获取会话最终报告"""
        session = self.sessions.get(session_id)
        if not session:
            return None
        return session.summary
