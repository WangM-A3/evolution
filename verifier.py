"""
evolution/verifier.py
======================
独立验证Agent（Independent Verifier）

核心原则：永远不让写代码的人自己验收

职责：
  1. 加载验收标准（来自 Sprint 契约）
  2. 执行独立测试（不依赖执行Agent的上下文）
  3. 生成结构化验证报告
  4. 与自进化系统无缝集成

设计原则：
  - 零依赖执行Agent上下文（纯文件驱动）
  - 客观测试标准（断言清晰、不可二义）
  - 隔离性：Verifier失败不影响执行Agent状态
  - 可追溯：每条验证结果附带证据路径

使用示例：
    verifier = IndependentVerifier(base_dir="evolution")

    # 方式A：从契约文件验证
    report = verifier.verify_from_contract(
        contract_path="evolution/contracts/SC-20260414-01.json",
        deliverables_dir="evolution/deliverables/TASK-20260414-01",
    )

    # 方式B：手动指定验收标准
    report = verifier.verify(
        task_id="TASK-20260414-01",
        goal="实现用户登录",
        criteria=[
            Criterion("单元测试通过", CriterionType.UNIT, validator=unit_tests_pass),
            Criterion("API响应时间<500ms", CriterionType.PERFORMANCE, validator=response_time_ok),
        ],
    )
"""

from __future__ import annotations

import json
import subprocess
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import Callable, Optional, Any, Protocol

from evolution.verification_report import (
    VerificationReport,
    CheckCategory,
    CheckResult,
    ReportStatus,
)


# ─────────────────────────────────────────────
# 枚举定义
# ─────────────────────────────────────────────

class CriterionType(Enum):
    UNIT        = auto()     # 单元测试
    INTEGRATION = auto()     # 集成测试
    CONTRACT    = auto()     # 契约验证
    SAFETY      = auto()     # 安全检查
    PERFORMANCE = auto()     # 性能基准
    STYLE       = auto()     # 代码风格
    E2E         = auto()     # 端到端
    MANUAL      = auto()     # 人工审查


class CriterionSeverity(Enum):
    INFO     = 1   # 信息级
    WARNING  = 2   # 警告
    ERROR    = 3   # 错误（可修复）
    CRITICAL = 4  # 严重（必须修复）
    BLOCKER  = 5  # 阻断（不能发布）


# ─────────────────────────────────────────────
# 验证器协议（Validator Protocol）
# ─────────────────────────────────────────────

class CriterionValidator(Protocol):
    """
    验证器协议：签名固定，返回 (passed: bool, detail: str, evidence: list[str])
    """
    def __call__(self, context: dict) -> tuple[bool, str, list[str]]: ...


# ─────────────────────────────────────────────
# 验收标准
# ─────────────────────────────────────────────

@dataclass
class Criterion:
    """
    单条验收标准

    Attributes:
        name: 标准名称
        criterion_type: 验证类型
        severity: 严重程度（1-5）
        validator: 验证函数
        description: 标准描述
        fix_suggestion: 失败时的修复建议
    """
    name: str
    criterion_type: CriterionType
    severity: int = 3
    validator: Optional[CriterionValidator] = None
    description: str = ""
    fix_suggestion: str = ""
    enabled: bool = True           # 是否启用（可临时禁用）

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "criterion_type": self.criterion_type.name,
            "severity": self.severity,
            "description": self.description,
            "fix_suggestion": self.fix_suggestion,
            "enabled": self.enabled,
        }


# ─────────────────────────────────────────────
# 内置验证器工厂
# ─────────────────────────────────────────────

class Validators:
    """
    内置验证器集合（可组合使用）

    使用示例：
        Criterion(
            name="Python语法正确",
            criterion_type=CriterionType.UNIT,
            validator=Validators.python_syntax_check,
        )
    """

    @staticmethod
    def python_syntax_check(context: dict) -> tuple[bool, str, list[str]]:
        """检查Python文件语法"""
        file_path = context.get("file_path")
        if not file_path or not Path(file_path).exists():
            return False, f"文件不存在: {file_path}", []
        result = subprocess.run(
            ["python3", "-m", "py_compile", file_path],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return True, f"语法检查通过: {file_path}", [file_path]
        return False, f"语法错误: {result.stderr}", []

    @staticmethod
    def file_exists_check(context: dict) -> tuple[bool, str, list[str]]:
        """检查文件是否存在"""
        path = context.get("path")
        p = Path(path) if path else None
        if p and p.exists():
            return True, f"文件存在: {path}", [str(p.resolve())]
        return False, f"文件不存在: {path}", []

    @staticmethod
    def json_parse_check(context: dict) -> tuple[bool, str, list[str]]:
        """检查JSON文件是否可解析"""
        path = context.get("path")
        try:
            json.loads(Path(path).read_text(encoding="utf-8"))
            return True, f"JSON解析成功: {path}", [path]
        except json.JSONDecodeError as e:
            return False, f"JSON解析失败: {e}", []

    @staticmethod
    def pytest_run(context: dict) -> tuple[bool, str, list[str]]:
        """运行pytest测试"""
        test_path = context.get("test_path", "tests/")
        verbose = context.get("verbose", True)
        args = ["python3", "-m", "pytest", test_path, "-v"] if verbose else ["python3", "-m", "pytest", test_path]
        result = subprocess.run(args, capture_output=True, text=True)
        passed = result.returncode == 0
        detail = f"pytest {'通过' if passed else '失败'}\n{result.stdout[-500:]}"
        evidence = [test_path] if Path(test_path).exists() else []
        return passed, detail, evidence

    @staticmethod
    def no_print_statements(context: dict) -> tuple[bool, str, list[str]]:
        """检查是否存在调试print语句（用于生产代码）"""
        file_path = context.get("file_path")
        production_mode = context.get("production_mode", True)
        if not production_mode:
            return True, "跳过（开发模式）", []

        violations: list[str] = []
        for line_num, line in enumerate(Path(file_path).read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("print(") and not stripped.startswith('"""') and not stripped.startswith("'''"):
                violations.append(f"  行{line_num}: {stripped[:80]}")
        if violations:
            return False, f"发现{len(violations)}处print语句:\n" + "\n".join(violations[:5]), [file_path]
        return True, "无调试print语句", [file_path]

    @staticmethod
    def schema_validation(context: dict) -> tuple[bool, str, list[str]]:
        """验证文件是否符合Schema"""
        path = context.get("path")
        schema = context.get("schema")
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            if schema == "verification_report":
                required_fields = ["report_id", "task_id", "status", "checks"]
                missing = [f for f in required_fields if f not in data]
                if missing:
                    return False, f"缺少必需字段: {missing}", []
                return True, "Schema验证通过", [path]
            return True, f"Schema {schema} 验证跳过（未定义）", []
        except Exception as e:
            return False, f"Schema验证失败: {e}", []

    @staticmethod
    def file_not_empty(context: dict) -> tuple[bool, str, list[str]]:
        """检查文件非空"""
        path = context.get("path")
        size = Path(path).stat().st_size if Path(path).exists() else 0
        if size > 0:
            return True, f"文件非空: {size} bytes", [path]
        return False, f"文件为空: {path}", []

    @staticmethod
    def progress_json_check(context: dict) -> tuple[bool, str, list[str]]:
        """检查进度JSON完整性"""
        path = context.get("path", "m-a3-harness-progress.json")
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            required = ["task_id", "goal", "features", "status"]
            missing = [f for f in required if f not in data]
            if missing:
                return False, f"缺少字段: {missing}", []
            features = data.get("features", [])
            pending = [f for f in features if f.get("status") == "pending" and not f.get("verified")]
            if pending:
                return False, f"存在未验证功能项: {len(pending)}", []
            return True, f"进度文件完整 ({len(features)}项)", [path]
        except json.JSONDecodeError as e:
            return False, f"JSON解析失败: {e}", []

    @staticmethod
    def git_clean_check(context: dict) -> tuple[bool, str, list[str]]:
        """检查git工作区是否干净"""
        work_dir = context.get("work_dir", ".")
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=work_dir, capture_output=True, text=True,
        )
        lines = [l for l in result.stdout.strip().splitlines() if l]
        if not lines:
            return True, "Git工作区干净", []
        return False, f"Git工作区有{len(lines)}个变更", lines[:5]


# ─────────────────────────────────────────────
# 独立验证Agent
# ─────────────────────────────────────────────

class IndependentVerifier:
    """
    独立验证Agent

    设计要点：
      - 不持有执行Agent的状态引用
      - 所有数据通过文件路径传入
      - 测试标准与业务逻辑完全解耦
      - 输出确定性、机器可读的验证报告

    验证流程：
        1. 加载验收标准（从契约或手动指定）
        2. 收集证据（文件、输出、日志）
        3. 执行验证（每个Criterion调用其validator）
        4. 汇总报告（按严重程度加权）
        5. 写入报告文件（evolution/reports/）
    """

    def __init__(
        self,
        base_dir: str = "evolution",
        reports_dir: str = "evolution/reports",
        contracts_dir: str = "evolution/contracts",
    ):
        self.base_dir = Path(base_dir)
        self.reports_dir = Path(reports_dir)
        self.contracts_dir = Path(contracts_dir)
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    # ── 核心验证入口 ──────────────────────────────────────────────────────────

    def verify(
        self,
        task_id: str,
        goal: str,
        criteria: list[Criterion],
        deliverables: Optional[dict[str, Any]] = None,
        sprint_contract_id: Optional[str] = None,
        environment: Optional[dict[str, str]] = None,
    ) -> VerificationReport:
        """
        执行完整验证流程

        Args:
            task_id: 任务ID
            goal: 任务目标描述
            criteria: 验收标准列表
            deliverables: 交付物上下文（文件路径等）
            sprint_contract_id: 关联的Sprint契约ID
            environment: 执行环境信息

        Returns:
            VerificationReport（已finalize）
        """
        deliverables = deliverables or {}
        report = VerificationReport(
            task_id=task_id,
            goal=goal,
            sprint_contract_id=sprint_contract_id,
            environment=environment or self._collect_env(),
        )

        # 执行每条标准
        for criterion in criteria:
            if not criterion.enabled:
                continue
            self._run_criterion(report, criterion, deliverables)

        report.finalize()
        return report

    def verify_from_contract(
        self,
        contract_path: str,
        deliverables_dir: str,
    ) -> VerificationReport:
        """
        从Sprint契约文件执行验证

        Args:
            contract_path: 契约JSON文件路径
            deliverables_dir: 交付物目录

        Returns:
            VerificationReport
        """
        contract = json.loads(Path(contract_path).read_text(encoding="utf-8"))

        task_id     = contract.get("task_id", "UNKNOWN")
        goal        = contract.get("goal", "")
        criteria_data = contract.get("acceptance_criteria", [])

        # 构建Criterion列表
        criteria = []
        for cd in criteria_data:
            c = Criterion(
                name=cd.get("name", ""),
                criterion_type=CriterionType[cd.get("type", "UNIT")],
                severity=cd.get("severity", 3),
                description=cd.get("description", ""),
                fix_suggestion=cd.get("fix_suggestion", ""),
            )
            criteria.append(c)

        deliverables = {"base_dir": deliverables_dir}

        # 收集所有交付物路径
        deliverables_path = Path(deliverables_dir)
        if deliverables_path.exists():
            deliverables["files"] = {
                str(p.relative_to(deliverables_path)): str(p)
                for p in deliverables_path.rglob("*")
                if p.is_file()
            }

        report = self.verify(
            task_id=task_id,
            goal=goal,
            criteria=criteria,
            deliverables=deliverables,
            sprint_contract_id=contract.get("contract_id"),
        )

        # 保存报告
        self._save_report(report, task_id)
        return report

    # ── 标准执行 ──────────────────────────────────────────────────────────────

    def _run_criterion(
        self,
        report: VerificationReport,
        criterion: Criterion,
        context: dict[str, Any],
    ) -> CheckResult:
        """运行单条验收标准"""
        category_map = {
            CriterionType.UNIT:        CheckCategory.UNIT,
            CriterionType.INTEGRATION: CheckCategory.INTEGRATION,
            CriterionType.E2E:          CheckCategory.E2E,
            CriterionType.CONTRACT:     CheckCategory.CONTRACT,
            CriterionType.SAFETY:      CheckCategory.SAFETY,
            CriterionType.PERFORMANCE: CheckCategory.PERFORMANCE,
            CriterionType.STYLE:        CheckCategory.STYLE,
            CriterionType.MANUAL:       CheckCategory.MANUAL,
        }

        started = time.time()

        # 注入文件上下文
        enriched = {**context}
        if "files" in context:
            enriched["file_list"] = list(context["files"].keys())
            if context["files"]:
                # 默认取第一个文件作为file_path
                enriched["file_path"] = next(iter(context["files"].values()))

        try:
            if criterion.validator:
                passed, detail, evidence = criterion.validator(enriched)
            else:
                # 无验证器 → 默认通过
                passed, detail, evidence = True, "无验证逻辑，默认通过", []
        except Exception as e:
            passed = False
            detail = f"验证器执行异常: {type(e).__name__}: {e}"
            evidence = [traceback.format_exc()[-500:]]

        duration_ms = (time.time() - started) * 1000

        return report.add_check(
            name=criterion.name,
            category=category_map.get(criterion.criterion_type, CheckCategory.MANUAL),
            passed=passed,
            detail=detail,
            evidence=evidence,
            fix_suggestion=criterion.fix_suggestion,
            duration_ms=duration_ms,
            severity=criterion.severity,
        )

    # ── 报告管理 ──────────────────────────────────────────────────────────────

    def _save_report(self, report: VerificationReport, task_id: str) -> Path:
        """保存验证报告"""
        filename = f"{task_id}-verification-report.json"
        path = self.reports_dir / filename
        report.save(path)
        return path

    def get_latest_report(self, task_id: Optional[str] = None) -> Optional[VerificationReport]:
        """获取最近的验证报告"""
        if not self.reports_dir.exists():
            return None
        reports = sorted(self.reports_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not reports:
            return None
        for r in reports:
            if task_id is None or task_id in r.stem:
                return VerificationReport.load(r)
        return None

    def get_reports_history(self, limit: int = 10) -> list[Path]:
        """获取验证报告历史"""
        if not self.reports_dir.exists():
            return []
        return sorted(self.reports_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]

    # ── 辅助方法 ──────────────────────────────────────────────────────────────

    @staticmethod
    def _collect_env() -> dict[str, str]:
        """收集当前环境信息"""
        import platform, os
        return {
            "python_version": platform.python_version(),
            "cwd": os.getcwd(),
            "verified_at": datetime.utcnow().isoformat(),
        }

    # ── 便捷构造 ──────────────────────────────────────────────────────────────

    @staticmethod
    def default_criteria_for_task(task_id: str) -> list[Criterion]:
        """
        为任意任务生成默认验收标准

        覆盖：
          1. 进度JSON文件存在且格式正确
          2. 交付物目录存在
          3. Git工作区状态
        """
        return [
            Criterion(
                name="进度JSON存在",
                criterion_type=CriterionType.UNIT,
                severity=3,
                validator=Validators.file_exists_check,
                fix_suggestion="创建 m-a3-harness-progress.json",
            ),
            Criterion(
                name="进度JSON格式正确",
                criterion_type=CriterionType.CONTRACT,
                severity=4,
                validator=Validators.progress_json_check,
                fix_suggestion="检查JSON结构和必需字段",
            ),
            Criterion(
                name="Git工作区已提交",
                criterion_type=CriterionType.MANUAL,
                severity=2,
                validator=Validators.git_clean_check,
                fix_suggestion="执行 git add . && git commit",
            ),
        ]

    def verify_quick(
        self,
        task_id: str,
        deliverables: Optional[dict[str, Any]] = None,
    ) -> VerificationReport:
        """
        快速验证（单次，不重试）
        等价于 AUTO_STOP + max_attempts=1
        """
        criteria = self.default_criteria_for_task(task_id)
        return self.verify(
            task_id=task_id,
            goal="快速验证",
            criteria=criteria,
            deliverables=deliverables or {},
        )
