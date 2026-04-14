"""
evolution/verification_report.py
=================================
验证报告数据结构（Verification Report）

独立验证Agent产出的结构化验证报告。
与 verifier.py 配合使用，确保验证结果可序列化、可回溯。

报告生命周期：
  build() → add_check() → add_evidence() → finalize() → to_json() / to_dict()

使用示例：
    report = VerificationReport(task_id="TASK-20260414-001", goal="实现用户登录")
    report.add_check("单元测试", passed=True, detail="12/12通过")
    report.add_check("集成测试", passed=False, detail="登录接口超时", fix_suggestion="增加超时重试")
    report.finalize()
    print(report.summary())
"""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import Optional, Any


# ─────────────────────────────────────────────
# 枚举定义
# ─────────────────────────────────────────────

class ReportStatus(Enum):
    PENDING    = "pending"     # 验证尚未开始
    RUNNING    = "running"     # 验证进行中
    PASSED     = "passed"      # 全部通过
    FAILED     = "failed"      # 存在失败项
    PARTIAL    = "partial"     # 部分通过（有条件通过）
    SKIPPED    = "skipped"     # 跳过（无适用场景）

    def __str__(self):
        return self.value


class CheckCategory(Enum):
    UNIT        = "unit"           # 单元测试
    INTEGRATION = "integration"     # 集成测试
    E2E         = "e2e"             # 端到端测试
    CONTRACT    = "contract"        # 契约验证
    SAFETY      = "safety"          # 安全检查
    PERFORMANCE = "performance"     # 性能基准
    STYLE       = "style"           # 代码风格
    MANUAL      = "manual"          # 人工审查


# ─────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────

@dataclass
class CheckResult:
    """
    单项验证检查结果

    Attributes:
        name: 检查项名称
        category: 所属类别
        passed: 是否通过
        detail: 详细描述
        evidence: 证据（文件路径、输出片段、截图URL等）
        fix_suggestion: 如果失败，修复建议
        duration_ms: 执行耗时（毫秒）
        severity: 严重程度 1-5（1=info，5=critical）
    """
    name: str
    category: CheckCategory
    passed: bool
    detail: str = ""
    evidence: list[str] = field(default_factory=list)
    fix_suggestion: str = ""
    duration_ms: float = 0.0
    severity: int = 3                     # 默认中等严重
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.utcnow().isoformat()

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "category": self.category.value,
            "passed": self.passed,
            "detail": self.detail,
            "evidence": self.evidence,
            "fix_suggestion": self.fix_suggestion,
            "duration_ms": self.duration_ms,
            "severity": self.severity,
            "timestamp": self.timestamp,
        }


@dataclass
class VerificationReport:
    """
    完整验证报告

    使用示例：
        report = VerificationReport(
            task_id="TASK-20260414-001",
            goal="实现用户登录功能",
            sprint_contract_id="SC-20260414-01",
        )
        report.add_check(...)
        report.finalize()
        report.save("evolution/reports/TASK-20260414-001-report.json")
    """
    task_id: str
    goal: str
    report_id: str = ""
    sprint_contract_id: Optional[str] = None
    status: ReportStatus = ReportStatus.PENDING
    checks: list[CheckResult] = field(default_factory=list)
    started_at: str = ""
    ended_at: str = ""
    verifier_version: str = "1.0.0"
    environment: dict[str, str] = field(default_factory=dict)
    verdict: str = ""                     # 最终裁决（人类可读）
    recommendations: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.report_id:
            ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
            self.report_id = f"VR-{self.task_id}-{ts}"
        if not self.started_at:
            self.started_at = datetime.utcnow().isoformat()

    # ── 检查项管理 ────────────────────────────────────────────────────────────

    def add_check(
        self,
        name: str,
        category: CheckCategory,
        passed: bool,
        detail: str = "",
        evidence: Optional[list[str]] = None,
        fix_suggestion: str = "",
        duration_ms: float = 0.0,
        severity: int = 3,
    ) -> CheckResult:
        """
        添加一项验证检查

        Args:
            name: 检查项名称
            category: 所属类别
            passed: 是否通过
            detail: 详细描述
            evidence: 证据列表
            fix_suggestion: 修复建议（失败时填写）
            duration_ms: 执行耗时
            severity: 严重程度 1-5

        Returns:
            新建的 CheckResult 实例
        """
        check = CheckResult(
            name=name,
            category=category,
            passed=passed,
            detail=detail,
            evidence=evidence or [],
            fix_suggestion=fix_suggestion,
            duration_ms=duration_ms,
            severity=severity,
        )
        self.checks.append(check)
        return check

    def add_evidence(self, check_name: str, evidence: str) -> bool:
        """为指定检查项追加证据"""
        for check in self.checks:
            if check.name == check_name:
                check.evidence.append(evidence)
                return True
        return False

    # ── 状态计算 ──────────────────────────────────────────────────────────────

    def _compute_status(self) -> ReportStatus:
        if not self.checks:
            return ReportStatus.SKIPPED
        passed = sum(1 for c in self.checks if c.passed)
        total = len(self.checks)
        critical_failed = [c for c in self.checks if not c.passed and c.severity >= 4]
        non_critical_failed = [c for c in self.checks if not c.passed and c.severity < 4]

        if passed == total:
            return ReportStatus.PASSED
        elif critical_failed:
            return ReportStatus.FAILED
        elif non_critical_failed:
            return ReportStatus.PARTIAL
        return ReportStatus.PENDING

    # ── 最终化 ────────────────────────────────────────────────────────────────

    def finalize(self) -> "VerificationReport":
        """
        最终化报告：计算状态、生成裁决、保存时间戳
        最终化后不可再添加检查项
        """
        self.status = self._compute_status()
        self.ended_at = datetime.utcnow().isoformat()
        self.verdict = self._generate_verdict()
        return self

    def _generate_verdict(self) -> str:
        total = len(self.checks)
        passed = sum(1 for c in self.checks if c.passed)
        failed = [c for c in self.checks if not c.passed]

        if self.status == ReportStatus.PASSED:
            return f"✅ 验证通过 — {passed}/{total} 项全部通过"
        elif self.status == ReportStatus.FAILED:
            names = ", ".join(c.name for c in failed[:3])
            return f"❌ 验证失败 — {len(failed)}/{total} 项失败：{names}"
        elif self.status == ReportStatus.PARTIAL:
            names = ", ".join(c.name for c in failed[:3])
            return f"⚠️  部分通过 — {passed}/{total} 通过，{len(failed)} 项有条件通过：{names}"
        elif self.status == ReportStatus.SKIPPED:
            return "⚪ 验证跳过 — 无适用检查项"
        return f"🔄 验证进行中 — {passed}/{total} 项已验证"

    # ── 摘要 ──────────────────────────────────────────────────────────────────

    def summary(self) -> dict:
        """生成人类可读的摘要"""
        total = len(self.checks)
        passed = sum(1 for c in self.checks if c.passed)
        failed = [c for c in self.checks if not c.passed]
        total_duration = sum(c.duration_ms for c in self.checks)

        return {
            "report_id": self.report_id,
            "task_id": self.task_id,
            "status": self.status.value,
            "verdict": self.verdict,
            "total_checks": total,
            "passed": passed,
            "failed": len(failed),
            "total_duration_ms": total_duration,
            "critical_failures": [c.name for c in failed if c.severity >= 4],
            "recommendations": self.recommendations,
        }

    def category_breakdown(self) -> dict[str, dict]:
        """按类别统计"""
        breakdown: dict[str, dict] = {}
        for check in self.checks:
            cat = check.category.value
            if cat not in breakdown:
                breakdown[cat] = {"total": 0, "passed": 0, "failed": 0}
            breakdown[cat]["total"] += 1
            if check.passed:
                breakdown[cat]["passed"] += 1
            else:
                breakdown[cat]["failed"] += 1
        return breakdown

    # ── 序列化 ────────────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "report_id": self.report_id,
            "task_id": self.task_id,
            "goal": self.goal,
            "sprint_contract_id": self.sprint_contract_id,
            "status": self.status.value,
            "verdict": self.verdict,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "verifier_version": self.verifier_version,
            "environment": self.environment,
            "recommendations": self.recommendations,
            "checks": [c.to_dict() for c in self.checks],
            "summary": self.summary(),
            "category_breakdown": self.category_breakdown(),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def save(self, path: str | Path) -> Path:
        """保存到文件"""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.to_json(), encoding="utf-8")
        return p

    @classmethod
    def load(cls, path: str | Path) -> "VerificationReport":
        """从文件加载"""
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        report = cls(
            task_id=d["task_id"],
            goal=d["goal"],
            report_id=d["report_id"],
            sprint_contract_id=d.get("sprint_contract_id"),
            status=ReportStatus(d["status"]),
            started_at=d["started_at"],
            ended_at=d.get("ended_at", ""),
            recommendations=d.get("recommendations", []),
        )
        for cd in d.get("checks", []):
            check = CheckResult(
                name=cd["name"],
                category=CheckCategory(cd["category"]),
                passed=cd["passed"],
                detail=cd.get("detail", ""),
                evidence=cd.get("evidence", []),
                fix_suggestion=cd.get("fix_suggestion", ""),
                duration_ms=cd.get("duration_ms", 0.0),
                severity=cd.get("severity", 3),
                timestamp=cd.get("timestamp", ""),
            )
            report.checks.append(check)
        return report
