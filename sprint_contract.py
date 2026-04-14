"""
evolution/sprint_contract.py
============================
Sprint契约（Sprint Contract）

核心功能：任务开始前明确验收标准，JSON格式契约文件

契约三要素：
  1. WHAT   - 交付什么（deliverables）
  2. HOW    - 如何验证（acceptance_criteria）
  3. WHEN   - 何时完成（deadline）

契约生命周期：
  draft() → review() → approve() → execute() → verify() → close()

使用示例：
    # 起草契约
    contract = SprintContract.create(
        task_id="TASK-20260414-001",
        goal="实现用户登录功能",
        owner="M-A3",
    )
    contract.add_deliverable("代码", "src/auth/login.py", priority="must")
    contract.add_criterion("单元测试通过", criterion_type="unit", severity=4)
    contract.set_deadline(hours_from_now=24)
    contract.save()

    # 执行契约
    executor = ContractExecutor(contract)
    result = executor.execute()
    executor.close(result)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum, auto
from pathlib import Path
from typing import Optional, Any


# ─────────────────────────────────────────────
# 枚举定义
# ─────────────────────────────────────────────

class ContractStatus(Enum):
    DRAFT      = "draft"      # 起草中
    REVIEWING  = "reviewing"  # 评审中
    APPROVED   = "approved"   # 已批准
    EXECUTING  = "executing"  # 执行中
    VERIFYING  = "verifying"  # 验证中
    PASSED     = "passed"    # 验证通过
    FAILED     = "failed"    # 验证失败
    CLOSED     = "closed"    # 已关闭（无论成功失败）
    CANCELLED  = "cancelled" # 已取消


class DeliverablePriority(Enum):
    MUST     = auto()   # 必须交付
    SHOULD   = auto()   # 应该交付
    COULD    = auto()   # 可以交付
    WONT     = auto()   # 此次不交付


class CriterionType(str, Enum):
    UNIT        = "UNIT"
    INTEGRATION = "INTEGRATION"
    E2E         = "E2E"
    CONTRACT    = "CONTRACT"
    SAFETY      = "SAFETY"
    PERFORMANCE = "PERFORMANCE"
    STYLE       = "STYLE"
    MANUAL      = "MANUAL"


# ─────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────

@dataclass
class Deliverable:
    """交付物条目"""
    name: str
    path: str
    description: str = ""
    priority: str = DeliverablePriority.MUST.name
    status: str = "pending"    # pending | in_progress | done
    verified: bool = False
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AcceptanceCriterion:
    """验收标准条目"""
    name: str
    type: str = CriterionType.UNIT.value
    severity: int = 3           # 1-5
    description: str = ""
    validator_id: str = ""     # 关联的验证器ID
    threshold: Optional[Any] = None  # 阈值（如响应时间<500ms）
    fix_suggestion: str = ""
    passed: Optional[bool] = None
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["type"] = self.type
        return d


@dataclass
class ContractParty:
    """契约方"""
    name: str
    role: str                  # "owner" | "executor" | "reviewer" | "approver"
    agent_id: str = ""
    email: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ─────────────────────────────────────────────
# Sprint契约
# ─────────────────────────────────────────────

class SprintContract:
    """
    Sprint契约

    使用示例：
        contract = SprintContract.create(
            task_id="TASK-20260414-001",
            goal="实现搜索功能",
            owner="M-A3",
        )
        contract.add_deliverable("API实现", "src/search/api.py")
        contract.add_criterion("pytest通过", type="UNIT", severity=4)
        contract.set_deadline(hours_from_now=8)
        contract.approve()
        contract.save("evolution/contracts/SC-20260414-001.json")
    """

    _counter = 0

    def __init__(
        self,
        contract_id: str,
        task_id: str,
        goal: str,
        owner: str = "",
        status: ContractStatus = ContractStatus.DRAFT,
    ):
        self.contract_id = contract_id
        self.task_id = task_id
        self.goal = goal
        self.owner = owner
        self.status = status
        self.created_at = datetime.utcnow().isoformat()
        self.updated_at = self.created_at
        self.approved_at: Optional[str] = None
        self.deadline: Optional[str] = None
        self.parties: list[ContractParty] = []
        self.deliverables: list[Deliverable] = []
        self.acceptance_criteria: list[AcceptanceCriterion] = []
        self.notes: list[str] = []
        self.metadata: dict[str, Any] = {}

    @classmethod
    def create(
        cls,
        task_id: str,
        goal: str,
        owner: str = "M-A3",
        contract_id: Optional[str] = None,
    ) -> "SprintContract":
        """创建新契约"""
        if contract_id is None:
            cls._counter += 1
            ts = datetime.utcnow().strftime("%Y%m%d")
            contract_id = f"SC-{ts}-{cls._counter:03d}"
        contract = cls(contract_id, task_id, goal, owner)
        contract.add_party(owner, "owner")
        return contract

    # ── 契约管理 ──────────────────────────────────────────────────────────────

    def add_party(self, name: str, role: str, agent_id: str = "") -> "SprintContract":
        self.parties.append(ContractParty(name=name, role=role, agent_id=agent_id))
        return self

    def add_deliverable(
        self,
        name: str,
        path: str,
        description: str = "",
        priority: DeliverablePriority = DeliverablePriority.MUST,
    ) -> "SprintContract":
        self.deliverables.append(Deliverable(
            name=name,
            path=path,
            description=description,
            priority=priority.name,
        ))
        return self

    def add_criterion(
        self,
        name: str,
        type: str = CriterionType.UNIT.value,
        severity: int = 3,
        description: str = "",
        validator_id: str = "",
        threshold: Any = None,
        fix_suggestion: str = "",
    ) -> "SprintContract":
        self.acceptance_criteria.append(AcceptanceCriterion(
            name=name,
            type=type,
            severity=severity,
            description=description,
            validator_id=validator_id,
            threshold=threshold,
            fix_suggestion=fix_suggestion,
        ))
        return self

    def set_deadline(self, hours_from_now: float = 24) -> "SprintContract":
        """设置截止时间（距现在N小时）"""
        self.deadline = (datetime.utcnow() + timedelta(hours=hours_from_now)).isoformat()
        return self

    def add_note(self, note: str) -> "SprintContract":
        self.notes.append(f"[{datetime.utcnow().isoformat()}] {note}")
        return self

    # ── 状态流转 ──────────────────────────────────────────────────────────────

    def draft(self) -> "SprintContract":
        self.status = ContractStatus.DRAFT
        self.updated_at = datetime.utcnow().isoformat()
        return self

    def review(self) -> "SprintContract":
        if self.status != ContractStatus.DRAFT:
            raise ValueError(f"只能从DRAFT状态进入REVIEWING，当前: {self.status}")
        self.status = ContractStatus.REVIEWING
        self.updated_at = datetime.utcnow().isoformat()
        return self

    def approve(self, approver: str = "M-A3") -> "SprintContract":
        if self.status not in (ContractStatus.DRAFT, ContractStatus.REVIEWING):
            raise ValueError(f"无法从{self.status}批准")
        if not self.deliverables:
            raise ValueError("必须至少有一条交付物才能批准")
        if not self.acceptance_criteria:
            raise ValueError("必须至少有一条验收标准才能批准")
        self.status = ContractStatus.APPROVED
        self.approved_at = datetime.utcnow().isoformat()
        self.updated_at = self.approved_at
        self.add_party(approver, "approver")
        return self

    def start_execution(self) -> "SprintContract":
        self.status = ContractStatus.EXECUTING
        self.updated_at = datetime.utcnow().isoformat()
        return self

    def start_verification(self) -> "SprintContract":
        self.status = ContractStatus.VERIFYING
        self.updated_at = datetime.utcnow().isoformat()
        return self

    def mark_passed(self) -> "SprintContract":
        self.status = ContractStatus.PASSED
        self.updated_at = datetime.utcnow().isoformat()
        return self

    def fail(self, reason: str = "") -> "SprintContract":
        self.status = ContractStatus.FAILED
        self.add_note(f"失败原因: {reason}")
        self.updated_at = datetime.utcnow().isoformat()
        return self

    def close(self) -> "SprintContract":
        self.status = ContractStatus.CLOSED
        self.updated_at = datetime.utcnow().isoformat()
        return self

    def cancel(self) -> "SprintContract":
        self.status = ContractStatus.CANCELLED
        self.updated_at = datetime.utcnow().isoformat()
        return self

    # ── 进度计算 ─────────────────────────────────────────────────────────────

    def progress(self) -> dict:
        """计算契约执行进度"""
        total = len(self.deliverables)
        done = sum(1 for d in self.deliverables if d.status == "done")
        verified = sum(1 for d in self.deliverables if d.verified)
        criteria_total = len(self.acceptance_criteria)
        criteria_passed = sum(1 for c in self.acceptance_criteria if c.passed)

        # 超时检测
        overdue = False
        if self.deadline and self.status in (ContractStatus.EXECUTING, ContractStatus.VERIFYING):
            overdue = datetime.utcnow() > datetime.fromisoformat(self.deadline)

        return {
            "contract_id": self.contract_id,
            "status": self.status.value,
            "deliverables_total": total,
            "deliverables_done": done,
            "deliverables_verified": verified,
            "criteria_total": criteria_total,
            "criteria_passed": criteria_passed,
            "completion_rate": done / total if total else 0.0,
            "verification_rate": verified / total if total else 0.0,
            "overdue": overdue,
            "deadline": self.deadline,
        }

    # ── 序列化 ────────────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "contract_id": self.contract_id,
            "task_id": self.task_id,
            "goal": self.goal,
            "owner": self.owner,
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "approved_at": self.approved_at,
            "deadline": self.deadline,
            "parties": [p.to_dict() for p in self.parties],
            "deliverables": [d.to_dict() for d in self.deliverables],
            "acceptance_criteria": [c.to_dict() for c in self.acceptance_criteria],
            "notes": self.notes,
            "metadata": self.metadata,
            "progress": self.progress(),
        }

    def save(self, path: Optional[str] = None) -> Path:
        """保存契约到文件"""
        if path is None:
            path = f"evolution/contracts/{self.contract_id}.json"
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return p

    @classmethod
    def load(cls, path: str) -> "SprintContract":
        """从文件加载契约"""
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        contract = cls(
            contract_id=d["contract_id"],
            task_id=d["task_id"],
            goal=d["goal"],
            owner=d.get("owner", ""),
            status=ContractStatus(d.get("status", "draft")),
        )
        contract.created_at = d.get("created_at", "")
        contract.updated_at = d.get("updated_at", "")
        contract.approved_at = d.get("approved_at")
        contract.deadline = d.get("deadline")
        contract.notes = d.get("notes", [])
        contract.metadata = d.get("metadata", {})
        for pd in d.get("parties", []):
            contract.parties.append(ContractParty(**pd))
        for dd in d.get("deliverables", []):
            contract.deliverables.append(Deliverable(**dd))
        for cd in d.get("acceptance_criteria", []):
            contract.acceptance_criteria.append(AcceptanceCriterion(**cd))
        return contract

    # ── 契约工厂 ──────────────────────────────────────────────────────────────

    @staticmethod
    def from_verifier_report(report_path: str) -> "SprintContract":
        """
        从验证报告反向生成契约（用于复盘）
        """
        from evolution.verification_report import VerificationReport
        report = VerificationReport.load(report_path)
        contract = SprintContract.create(
            task_id=report.task_id,
            goal=report.goal,
        )
        for check in report.checks:
            contract.add_criterion(
                name=check.name,
                type=check.category.value.upper(),
                severity=check.severity,
                description=check.detail,
                fix_suggestion=check.fix_suggestion,
            )
        contract.approve()
        return contract


# ─────────────────────────────────────────────
# 契约执行器
# ─────────────────────────────────────────────

class ContractExecutor:
    """
    契约执行器

    将契约与StopHook绑定，实现：
      契约启动 → 执行业务逻辑 → StopHook验证 → 契约关闭

    使用示例：
        executor = ContractExecutor(contract)
        executor.execute()           # 开始执行
        # ... 执行业务逻辑 ...
        result = executor.verify()   # 验证
        executor.close(result)       # 关闭契约
    """

    def __init__(self, contract: SprintContract):
        self.contract = contract
        self.verification_result = None

    def execute(self) -> SprintContract:
        """标记契约开始执行"""
        self.contract.start_execution()
        self.contract.save()
        return self.contract

    def verify(self, verifier) -> SprintContract:
        """执行验证"""
        self.contract.start_verification()
        self.contract.save()

        # 使用契约中的标准执行验证
        from evolution.verifier import Criterion, CriterionType
        criteria = [
            Criterion(
                name=c.name,
                criterion_type=CriterionType[c.type],
                severity=c.severity,
                fix_suggestion=c.fix_suggestion,
            )
            for c in self.contract.acceptance_criteria
        ]

        self.verification_result = verifier.verify(
            task_id=self.contract.task_id,
            goal=self.contract.goal,
            criteria=criteria,
            deliverables={"base_dir": "."},
        )

        # 更新契约中的标准状态
        result_map = {check.name: check for check in self.verification_result.checks}
        for criterion in self.contract.acceptance_criteria:
            if criterion.name in result_map:
                criterion.passed = result_map[criterion.name].passed
                criterion.evidence = result_map[criterion.name].evidence

        if self.verification_result.status == ReportStatus.PASSED:
            self.contract.mark_passed()
        else:
            self.contract.fail(self.verification_result.verdict)

        self.contract.save()
        return self.contract

    def close(self, result: SprintContract) -> SprintContract:
        """关闭契约"""
        result.close()
        result.save()
        return result
