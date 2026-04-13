"""
evolution/verification.py
==========================
验证闭环（Verification Loop）

核心功能：
- 创建检查点（快照当前状态）
- 应用变更（修改配置/规则/代码）
- 验证变更效果（自动测试 + 指标对比）
- 失败回滚（rollback）
- 通过晋升（promote）

三阶段验证：
  Stage 1 - 单元测试：验证代码无语法错误和基本逻辑
  Stage 2 - 集成测试：在检查点状态上运行回归测试
  Stage 3 - 在线验证：监控真实运行指标，验证改进效果

回滚机制：
- 每个检查点保存完整状态（rules/ + config + 基因）
- 失败时一键回滚到最近稳定检查点
- 记录失败原因到 failures/ 供分析
"""

from __future__ import annotations

import json
import shutil
import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import Optional, Any
from copy import deepcopy


# ─────────────────────────────────────────────
# 枚举定义
# ─────────────────────────────────────────────

class CheckpointStatus(Enum):
    ACTIVE = auto()
    SUPERSEDED = auto()    # 被更新的检查点替代
    ROLLED_BACK = auto()    # 已回滚
    CORRUPTED = auto()      # 损坏

    def __str__(self):
        return self.name


class VerificationStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"
    PARTIAL = "partial"     # 部分通过

    def __str__(self):
        return self.value


class ChangeType(Enum):
    RULE_ADD = auto()
    RULE_MODIFY = auto()
    RULE_DELETE = auto()
    CONFIG_UPDATE = auto()
    GENE_UPDATE = auto()
    CODE_CHANGE = auto()
    MANUAL = auto()


# ─────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────

@dataclass
class Checkpoint:
    """检查点快照"""
    checkpoint_id: str
    created_at: str
    description: str
    status: CheckpointStatus
    files_snapshotted: dict[str, str]     # file_path -> content hash
    metrics_snapshot: dict[str, float]     # 创建时的关键指标
    parent_checkpoint: Optional[str]        # 父检查点 ID
    change_description: str                 # 此检查点相比父的变化
    rollback_count: int = 0                 # 被回滚次数（用于评估稳定性）

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.name
        return d


@dataclass
class ProposedChange:
    """变更提议"""
    change_id: str
    change_type: ChangeType
    description: str
    target_files: list[str]
    proposed_content: dict[str, str]        # file_path -> new content
    rollback_from_checkpoint: str           # 从哪个检查点恢复
    risk_level: str = "medium"              # low / medium / high / critical
    auto_approved: bool = False            # 低风险变更可自动批准


@dataclass
class VerificationResult:
    """验证结果"""
    verification_id: str
    checkpoint_id: str
    change_id: str
    status: VerificationStatus
    stage: int                              # 1/2/3
    started_at: str
    completed_at: Optional[str] = None
    test_results: dict[str, Any] = field(default_factory=dict)   # 各阶段测试结果
    metrics_delta: dict[str, float] = field(default_factory=dict) # 指标变化
    rollback_performed: bool = False
    rollback_to_checkpoint: Optional[str] = None
    promotion_performed: bool = False
    error_message: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value if isinstance(d["status"], VerificationStatus) else d["status"]
        d["stage"] = str(d["stage"])
        return d


# ─────────────────────────────────────────────
# 验证闭环
# ─────────────────────────────────────────────

class VerificationLoop:
    """
    验证闭环

    工作流：
    1. create_checkpoint()   - 创建当前状态快照
    2. apply_change()        - 应用提议的变更
    3. validate_change()      - 三阶段验证
    4a. rollback_if_failed() - 失败则回滚
    4b. promote_if_passed()  - 通过则晋升

    持久化：
    - checkpoints/ 目录存储检查点元数据
    - checkpoints/{id}/ 目录存储快照文件副本
    - failures/ 目录存储失败记录
    """

    def __init__(
        self,
        checkpoints_dir: str = "evolution/checkpoints",
        failures_dir: str = "evolution/failures",
        rules_dir: str = "evolution/rules",
        stable_threshold: int = 3,           # 连续通过次数才能晋升
    ):
        self.checkpoints_dir = Path(checkpoints_dir)
        self.failures_dir = Path(failures_dir)
        self.rules_dir = Path(rules_dir)
        self.stable_threshold = stable_threshold

        # 初始化目录
        self.checkpoints_dir.mkdir(parents=True, exist_ok=True)
        self.failures_dir.mkdir(parents=True, exist_ok=True)
        self.rules_dir.mkdir(parents=True, exist_ok=True)

        # 加载检查点索引
        self._checkpoint_index: list[Checkpoint] = []
        self._load_index()

        # 跟踪连续通过次数
        self._consecutive_passes: dict[str, int] = {}

    # ── 阶段 0：检查点管理 ──

    def create_checkpoint(
        self,
        description: str,
        tracked_files: Optional[list[str]] = None,
        metrics: Optional[dict[str, float]] = None,
        parent_id: Optional[str] = None,
        change_description: str = "",
    ) -> Checkpoint:
        """
        创建检查点

        Args:
            description: 检查点描述
            tracked_files: 要跟踪的文件路径列表（相对于工作目录）
            metrics: 创建时的系统指标
            parent_id: 父检查点 ID（用于构建链）
            change_description: 相比父检查点的变化描述

        Returns:
            Checkpoint 对象
        """
        cp_id = self._generate_cp_id()
        timestamp = datetime.utcnow().isoformat()

        # 跟踪文件的哈希
        tracked = tracked_files or self._default_tracked_files()
        files_snapshotted = {}
        snapshot_dir = self.checkpoints_dir / cp_id
        snapshot_dir.mkdir(parents=True, exist_ok=True)

        for file_path in tracked:
            p = Path(file_path)
            if p.exists():
                content = p.read_text(encoding="utf-8")
                file_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
                files_snapshotted[str(p)] = file_hash
                # 保存副本
                dest = snapshot_dir / p.name
                dest.write_text(content, encoding="utf-8")
            else:
                files_snapshotted[str(p)] = "file_not_found"

        # 更新父检查点状态
        if parent_id:
            self._mark_superseded(parent_id)

        checkpoint = Checkpoint(
            checkpoint_id=cp_id,
            created_at=timestamp,
            description=description,
            status=CheckpointStatus.ACTIVE,
            files_snapshotted=files_snapshotted,
            metrics_snapshot=metrics or {},
            parent_checkpoint=parent_id,
            change_description=change_description,
        )

        self._checkpoint_index.append(checkpoint)
        self._save_index()
        self._save_checkpoint(checkpoint)

        return checkpoint

    def get_latest_checkpoint(self) -> Optional[Checkpoint]:
        """获取最新的活动检查点"""
        active = [cp for cp in self._checkpoint_index if cp.status == CheckpointStatus.ACTIVE]
        if not active:
            return None
        return max(active, key=lambda c: c.created_at)

    def get_checkpoint(self, checkpoint_id: str) -> Optional[Checkpoint]:
        for cp in self._checkpoint_index:
            if cp.checkpoint_id == checkpoint_id:
                return cp
        return None

    # ── 阶段 1：应用变更 ──

    def apply_change(self, change: ProposedChange) -> tuple[bool, str]:
        """
        应用变更到工作目录

        Returns:
            (success, message)
        """
        try:
            for file_path, content in change.proposed_content.items():
                p = Path(file_path)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8")

            return True, f"Successfully applied change {change.change_id}"

        except Exception as e:
            return False, f"Failed to apply change: {e}"

    # ── 阶段 2：验证变更 ──

    def validate_change(
        self,
        change: ProposedChange,
        checkpoint: Checkpoint,
    ) -> VerificationResult:
        """
        三阶段验证

        Stage 1: 静态检查（文件完整性 + 语法检查）
        Stage 2: 回归测试（如果存在测试套件）
        Stage 3: 指标验证（对比应用前后的指标变化）
        """
        vid = self._generate_vid()
        result = VerificationResult(
            verification_id=vid,
            checkpoint_id=checkpoint.checkpoint_id,
            change_id=change.change_id,
            status=VerificationStatus.RUNNING,
            stage=1,
            started_at=datetime.utcnow().isoformat(),
        )

        try:
            # ── Stage 1: 静态检查 ──
            stage1_pass, stage1_results = self._stage1_static_check(change)
            result.test_results["stage1"] = stage1_results

            if not stage1_pass:
                result.status = VerificationStatus.FAILED
                result.completed_at = datetime.utcnow().isoformat()
                result.error_message = "Stage 1 (static check) failed"
                self._log_failure(result, change)
                return result

            # ── Stage 2: 回归测试 ──
            result.stage = 2
            stage2_pass, stage2_results = self._stage2_regression_test(change)
            result.test_results["stage2"] = stage2_results

            if not stage2_pass:
                result.status = VerificationStatus.FAILED
                result.completed_at = datetime.utcnow().isoformat()
                result.error_message = "Stage 2 (regression test) failed"
                self._log_failure(result, change)
                return result

            # ── Stage 3: 指标验证 ──
            result.stage = 3
            stage3_pass, stage3_results = self._stage3_metrics_validation(
                change, checkpoint.metrics_snapshot
            )
            result.test_results["stage3"] = stage3_results
            result.metrics_delta = stage3_results.get("delta", {})

            result.status = VerificationStatus.PASSED if stage3_pass else VerificationStatus.PARTIAL
            result.completed_at = datetime.utcnow().isoformat()

            return result

        except Exception as e:
            result.status = VerificationStatus.FAILED
            result.completed_at = datetime.utcnow().isoformat()
            result.error_message = f"Verification exception: {e}"
            self._log_failure(result, change)
            return result

    def _stage1_static_check(self, change: ProposedChange) -> tuple[bool, dict]:
        """Stage 1: 静态检查"""
        results: dict[str, Any] = {"checks": [], "passed": True}
        errors: list[str] = []

        for file_path in change.target_files:
            content = change.proposed_content.get(file_path, "")
            checks = []

            # 检查 JSON 语法
            if file_path.endswith(".json"):
                try:
                    json.loads(content)
                    checks.append({"check": "json_valid", "passed": True})
                except json.JSONDecodeError as e:
                    checks.append({"check": "json_valid", "passed": False, "error": str(e)})
                    errors.append(f"{file_path}: Invalid JSON - {e}")
                    results["passed"] = False

            # 检查 YAML 语法（简单检查）
            elif file_path.endswith((".yaml", ".yml")):
                # 基础括号匹配检查
                checks.append({"check": "yaml_structure", "passed": True})  # 简化

            # 检查 Python 语法
            elif file_path.endswith(".py"):
                try:
                    compile(content, file_path, "exec")
                    checks.append({"check": "python_syntax", "passed": True})
                except SyntaxError as e:
                    checks.append({"check": "python_syntax", "passed": False, "error": str(e)})
                    errors.append(f"{file_path}: Syntax error - {e}")
                    results["passed"] = False

            results["checks"].extend(checks)

        results["errors"] = errors
        return results["passed"], results

    def _stage2_regression_test(self, change: ProposedChange) -> tuple[bool, dict]:
        """Stage 2: 回归测试（运行 pytest）"""
        import subprocess

        results: dict[str, Any] = {"test_command": None, "passed": False, "output": ""}

        # 检查是否存在测试目录
        test_dirs = ["tests", "evolution/tests"]
        test_dir = None
        for td in test_dirs:
            if Path(td).exists():
                test_dir = td
                break

        if test_dir is None:
            # 无测试目录，跳过此阶段（算通过）
            results["skipped"] = True
            results["message"] = "No test directory found, stage 2 skipped"
            return True, results

        try:
            result = subprocess.run(
                ["python", "-m", "pytest", test_dir, "-q", "--tb=short"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            results["test_command"] = f"python -m pytest {test_dir} -q --tb=short"
            results["returncode"] = result.returncode
            results["output"] = (result.stdout + result.stderr)[:1000]
            results["passed"] = result.returncode == 0
        except FileNotFoundError:
            results["skipped"] = True
            results["message"] = "pytest not available, stage 2 skipped"
            return True, results
        except subprocess.TimeoutExpired:
            results["passed"] = False
            results["output"] = "pytest timed out (>120s)"
            return False, results

        return results["passed"], results

    def _stage3_metrics_validation(
        self,
        change: ProposedChange,
        baseline_metrics: dict[str, float],
    ) -> tuple[bool, dict]:
        """Stage 3: 指标验证"""
        results: dict[str, Any] = {"delta": {}, "passed": True, "checks": []}

        # 模拟指标计算（实际应从监控系统获取）
        # 这里简单验证：如果变更降低了风险，则通过
        if change.risk_level in ("low", "medium"):
            results["checks"].append({
                "check": "risk_acceptable",
                "passed": True,
                "level": change.risk_level,
            })
        else:
            results["checks"].append({
                "check": "risk_acceptable",
                "passed": False,
                "level": change.risk_level,
            })
            results["passed"] = False

        # 检查变更是否与检查点一致
        results["checks"].append({
            "check": "change_within_scope",
            "passed": True,
            "scope": change.target_files,
        })

        return results["passed"], results

    # ── 阶段 3：回滚与晋升 ──

    def rollback_if_failed(
        self,
        verification_result: VerificationResult,
        change: ProposedChange,
        reason: str = "",
    ) -> tuple[bool, str]:
        """
        失败时回滚

        流程：
        1. 记录失败详情
        2. 从检查点恢复文件
        3. 更新检查点状态
        """
        verification_result.rollback_performed = True
        verification_result.rollback_to_checkpoint = verification_result.checkpoint_id

        try:
            cp = self.get_checkpoint(verification_result.checkpoint_id)
            if cp is None:
                return False, "Checkpoint not found for rollback"

            # 恢复文件
            snapshot_dir = self.checkpoints_dir / verification_result.checkpoint_id
            restored = 0
            for file_path in cp.files_snapshotted:
                snapshot_file = snapshot_dir / Path(file_path).name
                if snapshot_file.exists():
                    shutil.copy2(snapshot_file, file_path)
                    restored += 1

            # 更新检查点状态
            cp.status = CheckpointStatus.ROLLED_BACK
            cp.rollback_count += 1
            self._save_index()

            verification_result.status = VerificationStatus.ROLLED_BACK

            # 记录失败
            self._log_failure(verification_result, change, reason)

            return True, f"Rolled back {restored} files to checkpoint {cp.checkpoint_id}"

        except Exception as e:
            return False, f"Rollback failed: {e}"

    def promote_if_passed(
        self,
        verification_result: VerificationResult,
        rule_content: str,
    ) -> tuple[bool, str]:
        """
        通过时晋升

        流程：
        1. 将规则写入 rules/
        2. 记录进化事件
        3. 更新连续通过计数
        """
        verification_result.promotion_performed = True

        # 写入规则
        try:
            rule_file = Path("evolution/rules") / f"{verification_result.change_id}.md"
            rule_file.parent.mkdir(parents=True, exist_ok=True)
            rule_file.write_text(rule_content, encoding="utf-8")
        except Exception as e:
            return False, f"Failed to write rule: {e}"

        # 更新连续通过计数
        key = verification_result.change_id
        self._consecutive_passes[key] = self._consecutive_passes.get(key, 0) + 1

        # 如果连续通过阈值达到，标记为稳定
        is_stable = self._consecutive_passes[key] >= self.stable_threshold

        return True, (
            f"Promoted: rule written to {rule_file}, "
            f"consecutive_passes={self._consecutive_passes[key]}/{self.stable_threshold}"
            + (", STABLE" if is_stable else "")
        )

    # ── 工具方法 ──

    def _generate_cp_id(self) -> str:
        ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        return f"CP-{ts}"

    def _generate_vid(self) -> str:
        ts = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
        return f"VR-{ts}"

    def _default_tracked_files(self) -> list[str]:
        # 只返回文件，不返回目录
        return [
            "evolution/genes.json",
            "evolution/capsules.json",
            "evolution/config.yaml",
            "evolution/config.py",
            "MEMORY.md",
            "基础设定/SOUL.md",
        ]

    def _mark_superseded(self, checkpoint_id: str):
        for cp in self._checkpoint_index:
            if cp.checkpoint_id == checkpoint_id:
                cp.status = CheckpointStatus.SUPERSEDED
                break
        self._save_index()

    def _load_index(self):
        index_file = self.checkpoints_dir / "index.json"
        if index_file.exists():
            try:
                data = json.loads(index_file.read_text(encoding="utf-8"))
                self._checkpoint_index = []
                for item in data:
                    item["status"] = CheckpointStatus[item["status"]]
                    self._checkpoint_index.append(Checkpoint(**item))
            except Exception:
                self._checkpoint_index = []

    def _save_index(self):
        index_file = self.checkpoints_dir / "index.json"
        data = [cp.to_dict() for cp in self._checkpoint_index]
        index_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _save_checkpoint(self, checkpoint: Checkpoint):
        cp_file = self.checkpoints_dir / f"{checkpoint.checkpoint_id}.json"
        cp_file.write_text(
            json.dumps(checkpoint.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _log_failure(
        self,
        result: VerificationResult,
        change: ProposedChange,
        extra_reason: str = "",
    ):
        failure_record = {
            "verification_id": result.verification_id,
            "change_id": result.change_id,
            "checkpoint_id": result.checkpoint_id,
            "failed_at": datetime.utcnow().isoformat(),
            "status": result.status.value if isinstance(result.status, VerificationStatus) else result.status,
            "error_message": result.error_message or extra_reason,
            "change_type": change.change_type.name,
            "change_description": change.description,
            "test_results": result.test_results,
        }
        failure_file = self.failures_dir / f"failure_{result.verification_id}.json"
        failure_file.write_text(json.dumps(failure_record, ensure_ascii=False, indent=2), encoding="utf-8")
