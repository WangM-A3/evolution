"""
evolution/simplification_audit.py
=================================
定期简化审查（Simplification Audit）

核心理念：每个组件都应有"到期日"，无价值组件自动清理

核心功能：
  1. 组件到期日管理（每个组件标注expire_date）
  2. 价值评估（usage_count + staleness_score）
  3. 自动dead_weight检测
  4. 简化建议生成

到期日机制：
  每个进化产物（规则、能力胶囊、检查点等）都有一个到期日
  到期后需要重新评估：
    - 继续使用 → 续期
    - 价值下降 → 归档/删除
    - 被新产物替代 → 废弃

简化等级：
  KEEP       - 活跃使用，继续保留
  REVIEW     - 需要review（即将到期或使用率下降）
  ARCHIVE    - 归档（不再使用但保留历史）
  DELETE     - 删除（确认无用）

使用示例：
    auditor = SimplificationAuditor(base_dir="evolution")

    # 执行完整审查
    report = auditor.full_audit()

    # 获取dead_weight列表
    dead_weights = auditor.find_dead_weights(threshold_days=30)

    # 简化建议
    actions = auditor.suggest_simplifications()
    for action in actions:
        print(f"{action.action}: {action.target} — {action.reason}")
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

class SimplifyAction(Enum):
    KEEP      = auto()   # 保留
    REVIEW    = auto()   # 需要review
    RENEW     = auto()   # 续期
    ARCHIVE   = auto()   # 归档
    DELETE    = auto()   # 删除


class ComponentType(Enum):
    RULE           = auto()   # 生成的规则
    CAPSULE        = auto()   # 能力胶囊
    CHECKPOINT      = auto()   # 检查点
    PATTERN        = auto()   # 模式
    SESSION        = auto()   # 会话记录
    REPORT         = auto()   # 验证报告
    CONTRACT       = auto()   # Sprint契约


# ─────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────

@dataclass
class ComponentMetadata:
    """
    组件元数据（每个可进化产物都应携带此元数据）

    使用方法：
      1. 创建产物时附加此元数据
      2. auditor据此进行到期审查
    """
    component_id: str
    component_type: str          # ComponentType.name
    name: str
    created_at: str
    expire_date: str             # 到期日期（ISO格式）
    last_used: Optional[str] = None
    last_reviewed: Optional[str] = None
    usage_count: int = 0
    staleness_score: float = 0.0  # 0.0（活跃）~ 1.0（死代码）
    version: str = "1.0.0"
    parent_id: Optional[str] = None  # 替代了哪个旧组件
    superseded_by: Optional[str] = None  # 被哪个新组件替代
    tags: list[str] = field(default_factory=list)
    note: str = ""
    audit_status: str = SimplifyAction.KEEP.name

    def to_dict(self) -> dict:
        d = asdict(self)
        d["component_type"] = self.component_type
        d["audit_status"] = self.audit_status
        return d

    @classmethod
    def with_defaults(
        cls,
        component_id: str,
        component_type: str,
        name: str,
        days_until_expire: int = 30,
    ) -> "ComponentMetadata":
        """创建带默认值的元数据"""
        now = datetime.utcnow()
        return cls(
            component_id=component_id,
            component_type=component_type,
            name=name,
            created_at=now.isoformat(),
            expire_date=(now + timedelta(days=days_until_expire)).isoformat(),
        )


@dataclass
class AuditResult:
    """审查结果条目"""
    component_id: str
    component_type: str
    name: str
    action: SimplifyAction
    reason: str
    staleness_score: float
    days_until_expire: int
    usage_count: int
    suggestion: str = ""

    def to_dict(self) -> dict:
        return {
            "component_id": self.component_id,
            "component_type": self.component_type,
            "name": self.name,
            "action": self.action.name,
            "reason": self.reason,
            "staleness_score": round(self.staleness_score, 3),
            "days_until_expire": self.days_until_expire,
            "usage_count": self.usage_count,
            "suggestion": self.suggestion,
        }


@dataclass
class SimplificationReport:
    """完整简化审查报告"""
    report_id: str
    audit_time: str
    components_scanned: int
    audit_results: list[AuditResult]
    dead_weights: list[AuditResult]       # 需要清理的组件
    keep_list: list[AuditResult]
    review_list: list[AuditResult]
    summary: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "report_id": self.report_id,
            "audit_time": self.audit_time,
            "components_scanned": self.components_scanned,
            "dead_weights": [r.to_dict() for r in self.dead_weights],
            "keep_list": [r.to_dict() for r in self.keep_list],
            "review_list": [r.to_dict() for r in self.review_list],
            "summary": self.summary,
        }

    def save(self, path: str) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return p


# ─────────────────────────────────────────────
# 简化审查器
# ─────────────────────────────────────────────

class SimplificationAuditor:
    """
    定期简化审查器

    功能：
      - 扫描所有evolution/目录下的组件
      - 计算每个组件的staleness_score
      - 识别dead_weight（长期无使用）
      - 生成简化建议

    staleness_score计算公式：
      staleness = (
          days_since_used / 30 * 0.4          # 久未使用
        + days_until_expire / 30 * 0.3        # 即将到期
        + (1 - usage_rate) * 0.3             # 使用频率低
      )
      其中 usage_rate = usage_count / max_age_days
    """

    def __init__(
        self,
        base_dir: str = "evolution",
        reports_dir: str = "evolution/audit_reports",
    ):
        self.base_dir = Path(base_dir)
        self.reports_dir = Path(reports_dir)
        self.reports_dir.mkdir(parents=True, exist_ok=True)

        # 各组件目录
        self._dirs = {
            ComponentType.RULE:         Path("evolution/rules"),
            ComponentType.CAPSULE:     Path("evolution/capsules"),
            ComponentType.CHECKPOINT:  Path("evolution/checkpoints"),
            ComponentType.CONTRACT:    Path("evolution/contracts"),
            ComponentType.REPORT:       Path("evolution/reports"),
            ComponentType.SESSION:     Path("evolution/sessions"),
        }

    # ── 核心审计 ──────────────────────────────────────────────────────────────

    def full_audit(self) -> SimplificationReport:
        """
        执行完整审查

        扫描所有组件目录，按以下规则分类：
          - staleness > 0.8 → DELETE
          - 0.5 < staleness ≤ 0.8 → ARCHIVE
          - days_until_expire ≤ 7 → REVIEW
          - usage_count == 0 且 created > 14天前 → REVIEW
          - 其他 → KEEP
        """
        all_results: list[AuditResult] = []

        # 扫描各目录
        for comp_type, dir_path in self._dirs.items():
            if dir_path.exists():
                results = self._audit_directory(dir_path, comp_type)
                all_results.extend(results)

        # 分类
        dead_weights = [r for r in all_results if r.action in (SimplifyAction.DELETE, SimplifyAction.ARCHIVE)]
        keep_list = [r for r in all_results if r.action == SimplifyAction.KEEP]
        review_list = [r for r in all_results if r.action == SimplifyAction.REVIEW]

        summary = {
            "total": len(all_results),
            "delete": len([r for r in all_results if r.action == SimplifyAction.DELETE]),
            "archive": len([r for r in all_results if r.action == SimplifyAction.ARCHIVE]),
            "review": len(review_list),
            "keep": len(keep_list),
        }

        report = SimplificationReport(
            report_id=f"AUDIT-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
            audit_time=datetime.utcnow().isoformat(),
            components_scanned=len(all_results),
            audit_results=all_results,
            dead_weights=dead_weights,
            keep_list=keep_list,
            review_list=review_list,
            summary=summary,
        )

        report.save(str(self.reports_dir / f"{report.report_id}.json"))
        return report

    def _audit_directory(
        self,
        dir_path: Path,
        comp_type: ComponentType,
    ) -> list[AuditResult]:
        """审计单个目录"""
        results = []

        for file_path in dir_path.rglob("*.json"):
            if file_path.name.startswith("."):
                continue

            try:
                data = json.loads(file_path.read_text(encoding="utf-8"))
                meta = self._extract_metadata(data, file_path, comp_type)
                result = self._audit_component(meta, file_path)
                results.append(result)
            except Exception:
                # 解析失败，视为潜在dead_weight
                results.append(AuditResult(
                    component_id=file_path.stem,
                    component_type=comp_type.name,
                    name=file_path.name,
                    action=SimplifyAction.REVIEW,
                    reason=f"无法解析JSON: {file_path}",
                    staleness_score=0.7,
                    days_until_expire=-999,
                    usage_count=0,
                    suggestion="检查文件格式或删除",
                ))

        return results

    def _extract_metadata(
        self,
        data: dict,
        file_path: Path,
        comp_type: ComponentType,
    ) -> ComponentMetadata:
        """从文件数据提取元数据"""
        now = datetime.utcnow()

        # 多种数据格式兼容
        meta_dict = data.get("metadata", {}) if isinstance(data, dict) else {}
        created_str = (
            meta_dict.get("created_at")
            or data.get("created_at")
            or data.get("timestamp")
            or now.isoformat()
        )
        expire_str = (
            meta_dict.get("expire_date")
            or data.get("expire_date")
            or (now + timedelta(days=30)).isoformat()
        )
        last_used_str = (
            meta_dict.get("last_used")
            or data.get("last_used")
            or data.get("updated_at")
            or created_str
        )

        try:
            created = datetime.fromisoformat(created_str)
        except Exception:
            created = now

        try:
            expire = datetime.fromisoformat(expire_str)
        except Exception:
            expire = now + timedelta(days=30)

        try:
            last_used = datetime.fromisoformat(last_used_str)
        except Exception:
            last_used = created

        # 计算staleness
        days_since_used = (now - last_used).days
        days_until_expire = (expire - now).days
        usage_count = meta_dict.get("usage_count", data.get("usage_count", 0))
        max_age = max(1, (now - created).days)
        usage_rate = usage_count / max_age if max_age > 0 else 0

        staleness = min(1.0, (
            min(days_since_used, 90) / 90 * 0.4
            + max(0, -days_until_expire) / 30 * 0.3
            + max(0, 1 - usage_rate) * 0.3
        ))

        return ComponentMetadata(
            component_id=meta_dict.get("component_id", data.get("rule_id", data.get("id", file_path.stem))),
            component_type=comp_type.name,
            name=meta_dict.get("name", data.get("name", file_path.name)),
            created_at=created.isoformat(),
            expire_date=expire.isoformat(),
            last_used=last_used.isoformat(),
            last_reviewed=meta_dict.get("last_reviewed"),
            usage_count=usage_count,
            staleness_score=staleness,
            version=meta_dict.get("version", data.get("version", "1.0.0")),
            superseded_by=meta_dict.get("superseded_by", data.get("superseded_by")),
            tags=meta_dict.get("tags", data.get("tags", [])),
            note=meta_dict.get("note", ""),
        )

    def _audit_component(
        self,
        meta: ComponentMetadata,
        file_path: Path,
    ) -> AuditResult:
        """审计单个组件"""
        now = datetime.utcnow()
        try:
            expire = datetime.fromisoformat(meta.expire_date)
        except Exception:
            expire = now + timedelta(days=30)

        days_until_expire = (expire - now).days
        staleness = meta.staleness_score
        days_since_used = (
            (now - datetime.fromisoformat(meta.last_used))
            if meta.last_used else 999
        ).days

        # 决策规则
        if staleness > 0.8 or meta.superseded_by:
            action = SimplifyAction.DELETE
            reason = f"死代码（staleness={staleness:.2f}）" + (
                f" | 被{meta.superseded_by}替代" if meta.superseded_by else ""
            )
            suggestion = f"删除 {file_path}"
        elif 0.5 < staleness <= 0.8:
            action = SimplifyAction.ARCHIVE
            reason = f"价值下降（staleness={staleness:.2f}）"
            suggestion = f"归档 {file_path}"
        elif days_until_expire <= 7:
            action = SimplifyAction.REVIEW
            reason = f"即将到期（{days_until_expire}天后）"
            suggestion = f"续期或删除 {file_path}"
        elif meta.usage_count == 0 and days_since_used >= 14:
            action = SimplifyAction.REVIEW
            reason = f"创建后{days_since_used}天从未使用"
            suggestion = f"评估是否需要 {file_path}"
        else:
            action = SimplifyAction.KEEP
            reason = f"活跃使用（usage={meta.usage_count}, staleness={staleness:.2f}）"
            suggestion = f"继续保留"

        return AuditResult(
            component_id=meta.component_id,
            component_type=meta.component_type,
            name=meta.name,
            action=action,
            reason=reason,
            staleness_score=meta.staleness_score,
            days_until_expire=days_until_expire,
            usage_count=meta.usage_count,
            suggestion=suggestion,
        )

    # ── 便捷接口 ──────────────────────────────────────────────────────────────

    def find_dead_weights(self, threshold_days: int = 30) -> list[AuditResult]:
        """快速查找dead_weight组件"""
        results = self.full_audit()
        return [
            r for r in results.dead_weights
            if r.days_until_expire < -threshold_days or r.staleness_score > 0.8
        ]

    def suggest_simplifications(self) -> list[AuditResult]:
        """生成简化建议（不含KEEP项）"""
        results = self.full_audit()
        return [r for r in results.audit_results if r.action != SimplifyAction.KEEP]

    def apply_action(
        self,
        result: AuditResult,
        dry_run: bool = True,
    ) -> dict:
        """
        执行简化动作

        Args:
            result: 审计结果
            dry_run: True=仅预览，False=实际执行

        Returns:
            执行结果
        """
        if result.action == SimplifyAction.KEEP:
            return {"status": "skipped", "reason": "KEEP动作无需执行"}

        archive_dir = self.reports_dir / "archived"
        action_descriptions = {
            SimplifyAction.DELETE: ("删除文件", archive_dir / "deleted"),
            SimplifyAction.ARCHIVE: ("归档文件", archive_dir),
            SimplifyAction.REVIEW: ("标记待review", None),
            SimplifyAction.RENEW: ("续期30天", None),
        }

        desc, target_dir = action_descriptions.get(result.action, ("未知动作", None))

        # 查找文件
        file_path = self._find_component_path(result.component_id, result.component_type)

        if dry_run:
            return {
                "status": "dry_run",
                "action": desc,
                "target": str(file_path) if file_path else "未找到",
                "destination": str(target_dir) if target_dir else None,
            }

        if not file_path or not file_path.exists():
            return {"status": "not_found", "component_id": result.component_id}

        if result.action in (SimplifyAction.DELETE, SimplifyAction.ARCHIVE):
            target_dir.mkdir(parents=True, exist_ok=True)
            dest = target_dir / file_path.name
            file_path.rename(dest)
            return {"status": "done", "moved_to": str(dest)}

        return {"status": "not_implemented", "action": result.action.name}

    def _find_component_path(self, component_id: str, component_type: str) -> Optional[Path]:
        """查找组件文件路径"""
        try:
            ct = ComponentType[component_type]
            dir_path = self._dirs.get(ct)
            if dir_path and dir_path.exists():
                for f in dir_path.rglob("*.json"):
                    if component_id in f.stem or component_id in f.read_text():
                        return f
        except Exception:
            pass
        return None

    # ── 元数据管理 ─────────────────────────────────────────────────────────────

    @staticmethod
    def attach_metadata(
        file_path: str,
        metadata: ComponentMetadata,
    ) -> Path:
        """
        为现有文件附加元数据

        使用示例：
            SimplificationAuditor.attach_metadata(
                "evolution/rules/my_rule.json",
                ComponentMetadata.with_defaults(
                    component_id="RULE-001",
                    component_type="RULE",
                    name="我的规则",
                    days_until_expire=30,
                )
            )
        """
        p = Path(file_path)
        data = json.loads(p.read_text(encoding="utf-8"))
        if "metadata" not in data:
            data["metadata"] = {}
        data["metadata"].update(metadata.to_dict())
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return p
