"""
evolution/sharing.py
====================
能力共享机制（Capability Sharing）

参考 Accio Work：用户创建的 Skill 可以分享，能力库不断扩充

核心功能：
  export_capability()   → 将能力导出为 Skill 格式（SKILL.md + 元数据）
  import_capability()   → 从社区导入能力并集成到本地
  rate_capability()     → 对能力进行评分（质量/覆盖度/易用性）
  version_control()     → 能力的版本管理和升级

文件结构：
  evolution/capabilities/          # 导出的能力包目录
  evolution/capabilities/{name}/   # 各能力的独立目录
      SKILL.md                      # Skill 定义文档
      metadata.json                 # 元数据（版本/评分/标签）
      implementation/               # 可选：实现代码
      CHANGELOG.md                  # 版本变更记录
"""

from __future__ import annotations

import json
import hashlib
import shutil
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, Any
import logging

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────

@dataclass
class CapabilityRating:
    """能力评分"""
    overall: float          # 综合评分 0-1
    quality: float          # 质量分
    coverage: float          # 覆盖度分
    usability: float         # 易用性分
    ratings_count: int = 0   # 评分次数
    last_rated: Optional[str] = None


@dataclass
class CapabilityVersion:
    """能力版本"""
    version: str             # 语义化版本 "1.0.0"
    changelog: str           # 变更说明
    breaking: bool = False   # 是否破坏性变更
    published_at: str = ""


@dataclass
class CapabilityExport:
    """导出的能力包"""
    capability_id: str
    name: str
    description: str
    version: str
    tags: list[str]
    trigger_conditions: list[str]
    implementation_type: str       # "pattern" / "rule" / "workflow" / "baseline"
    skill_md: str                 # SKILL.md 内容
    metadata_json: str             # metadata.json 内容
    checksum: str                 # 内容校验和
    exported_at: str = ""
    author: str = "M-A3"

    def to_dict(self) -> dict:
        return asdict(self)


# ─────────────────────────────────────────────
# 能力共享管理器
# ─────────────────────────────────────────────

class CapabilitySharing:
    """
    能力共享管理器

    使用示例：
        sharing = CapabilitySharing(base_dir="evolution/capabilities")

        # 导出能力
        pkg = sharing.export_capability(
            capsule_id="CAP-PATTERN-001",
            name="数据库连接池优化",
            description="自动识别连接池耗尽并建议扩展",
            tags=["database", "performance"],
        )
        print(f"Exported to: {pkg.capability_id}")

        # 导入能力
        result = sharing.import_capability("evolution/capabilities/db-pool-opt")
        print(f"Imported: {result['capability_id']}")

        # 评分
        sharing.rate_capability("CAP-001", quality=0.9, coverage=0.8, usability=0.85)

        # 检查更新
        updates = sharing.version_control("CAP-001")
    """

    def __init__(
        self,
        base_dir: str = "evolution/capabilities",
        capabilities_registry: str = "evolution/capabilities_registry.json",
    ):
        self.base_dir = Path(base_dir)
        self.capabilities_registry = Path(capabilities_registry)

        # 初始化目录
        self.base_dir.mkdir(parents=True, exist_ok=True)

        # 初始化注册表
        self._init_registry()

    def _init_registry(self):
        """初始化能力注册表"""
        if not self.capabilities_registry.exists():
            self._save_registry({
                "schema_version": "1.0",
                "capabilities": {},
                "community_index": {},   # community_id → capability summary
                "last_updated": datetime.utcnow().isoformat(),
            })

    def _load_registry(self) -> dict:
        if self.capabilities_registry.exists():
            return json.loads(self.capabilities_registry.read_text(encoding="utf-8"))
        return {"capabilities": {}, "community_index": {}}

    def _save_registry(self, data: dict):
        data["last_updated"] = datetime.utcnow().isoformat()
        self.capabilities_registry.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ── 核心方法 ─────────────────────────────────────────────────────────────

    def export_capability(
        self,
        capsule_id: str,
        name: str,
        description: str,
        tags: Optional[list[str]] = None,
        trigger_conditions: Optional[list[str]] = None,
        implementation_type: str = "pattern",
        implementation_code: Optional[str] = None,
    ) -> CapabilityExport:
        """
        将本地能力（胶囊/模式）导出为可分享的 Skill 格式

        Args:
            capsule_id: 来源能力胶囊ID（从 capsules.json 读取）
            name: 能力名称
            description: 能力描述（50-300字）
            tags: 标签列表（用于社区发现）
            trigger_conditions: 触发条件列表
            implementation_type: 实现类型
            implementation_code: 可选的实现代码

        Returns:
            CapabilityExport 包
        """
        tags = tags or []
        trigger_conditions = trigger_conditions or []

        # 生成能力ID（基于 capsule_id 哈希）
        cap_id = f"SKL-{capsule_id[-12:].upper()}-{datetime.utcnow().strftime('%Y%m%d')}"
        version = "1.0.0"

        # 生成 SKILL.md 内容
        skill_md = self._generate_skill_md(
            cap_id, name, description, tags, trigger_conditions, version
        )

        # 生成 metadata.json
        metadata = {
            "capability_id": cap_id,
            "source_capsule_id": capsule_id,
            "name": name,
            "description": description,
            "version": version,
            "tags": tags,
            "trigger_conditions": trigger_conditions,
            "implementation_type": implementation_type,
            "author": "M-A3",
            "exported_at": datetime.utcnow().isoformat(),
            "rating": {"overall": 0.0, "quality": 0.0, "coverage": 0.0, "usability": 0.0, "ratings_count": 0},
            "installs": 0,
            "status": "published",
        }

        # 创建能力目录
        cap_dir = self.base_dir / cap_id
        cap_dir.mkdir(parents=True, exist_ok=True)

        # 写入文件
        (cap_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")
        (cap_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

        if implementation_code:
            impl_dir = cap_dir / "implementation"
            impl_dir.mkdir(exist_ok=True)
            (impl_dir / "main.py").write_text(implementation_code, encoding="utf-8")
            metadata["has_implementation"] = True
        else:
            metadata["has_implementation"] = False

        # 生成校验和
        content = skill_md + json.dumps(metadata, sort_keys=True)
        checksum = hashlib.sha256(content.encode()).hexdigest()[:16]

        # 更新注册表
        registry = self._load_registry()
        registry["capabilities"][cap_id] = {
            "name": name,
            "description": description,
            "version": version,
            "tags": tags,
            "path": str(cap_dir),
            "checksum": checksum,
            "exported_at": metadata["exported_at"],
            "status": "published",
        }
        self._save_registry(registry)

        logger.info(f"[CapabilitySharing] Exported: {cap_id} → {cap_dir}")

        return CapabilityExport(
            capability_id=cap_id,
            name=name,
            description=description,
            version=version,
            tags=tags,
            trigger_conditions=trigger_conditions,
            implementation_type=implementation_type,
            skill_md=skill_md,
            metadata_json=json.dumps(metadata, ensure_ascii=False, indent=2),
            checksum=checksum,
            exported_at=metadata["exported_at"],
        )

    def import_capability(
        self,
        source_path: str,
        import_type: str = "local",
    ) -> dict:
        """
        导入外部能力（本地文件或社区分享）

        Args:
            source_path: 能力包路径（本地目录或社区ID）
            import_type: "local" | "community"

        Returns:
            导入结果
        """
        source = Path(source_path)

        if import_type == "local":
            if not source.exists():
                raise FileNotFoundError(f"Capability package not found: {source_path}")

            # 读取 metadata.json
            metadata_path = source / "metadata.json"
            if not metadata_path.exists():
                raise ValueError(f"Invalid capability package: missing metadata.json")

            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            skill_md_path = source / "SKILL.md"
            skill_md = skill_md_path.read_text(encoding="utf-8") if skill_md_path.exists() else ""

        elif import_type == "community":
            # 从社区索引查询（模拟，实际从 API 获取）
            registry = self._load_registry()
            community = registry.get("community_index", {})
            summary = community.get(source_path)
            if not summary:
                raise ValueError(f"Community capability not found: {source_path}")
            metadata = summary
            skill_md = ""  # 社区导入时从 API 下载
        else:
            raise ValueError(f"Unknown import_type: {import_type}")

        cap_id = metadata.get("capability_id", f"IMPORT-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}")
        cap_dir = self.base_dir / cap_id
        cap_dir.mkdir(parents=True, exist_ok=True)

        # 复制文件
        if import_type == "local":
            shutil.copy2(metadata_path, cap_dir / "metadata.json")
            if skill_md_path.exists():
                shutil.copy2(skill_md_path, cap_dir / "SKILL.md")
            impl_src = source / "implementation"
            if impl_src.exists():
                shutil.copytree(impl_src, cap_dir / "implementation", dirs_exist_ok=True)

        # 注册到本地
        registry = self._load_registry()
        registry["capabilities"][cap_id] = {
            "name": metadata.get("name", "Imported"),
            "description": metadata.get("description", ""),
            "version": metadata.get("version", "1.0.0"),
            "tags": metadata.get("tags", []),
            "path": str(cap_dir),
            "source": import_type,
            "imported_at": datetime.utcnow().isoformat(),
            "status": "installed",
        }
        self._save_registry(registry)

        # 增加安装计数（社区导入时）
        if import_type == "community":
            self._increment_installs(cap_id)

        logger.info(f"[CapabilitySharing] Imported: {cap_id}")
        return {
            "capability_id": cap_id,
            "name": metadata.get("name"),
            "version": metadata.get("version"),
            "imported_at": datetime.utcnow().isoformat(),
            "path": str(cap_dir),
        }

    def rate_capability(
        self,
        capability_id: str,
        quality: Optional[float] = None,
        coverage: Optional[float] = None,
        usability: Optional[float] = None,
        overall: Optional[float] = None,
    ) -> dict:
        """
        对能力进行评分（0-1）

        Args:
            capability_id: 能力ID
            quality: 质量分（0-1）
            coverage: 覆盖度分（0-1）
            usability: 易用性分（0-1）
            overall: 综合评分（0-1，可覆盖单项）

        Returns:
            更新后的评分
        """
        registry = self._load_registry()
        cap = registry["capabilities"].get(capability_id)
        if not cap:
            raise ValueError(f"Capability not found: {capability_id}")

        metadata_path = Path(cap["path"]) / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
        rating = metadata.get("rating", {"overall": 0.0, "quality": 0.0,
                                          "coverage": 0.0, "usability": 0.0, "ratings_count": 0})

        n = rating["ratings_count"]
        new_rating = CapabilityRating(
            overall=rating.get("overall", 0.0),
            quality=rating.get("quality", 0.0),
            coverage=rating.get("coverage", 0.0),
            usability=rating.get("usability", 0.0),
            ratings_count=n,
        )

        # 更新各维度（滑动平均）
        for dim, val in [("quality", quality), ("coverage", coverage), ("usability", usability)]:
            if val is not None:
                old = getattr(new_rating, dim)
                setattr(new_rating, dim, (old * n + val) / (n + 1))

        # 综合评分
        if overall is not None:
            new_rating.overall = (new_rating.overall * n + overall) / (n + 1)
        else:
            new_rating.overall = (
                new_rating.quality * 0.4 +
                new_rating.coverage * 0.3 +
                new_rating.usability * 0.3
            )

        new_rating.ratings_count = n + 1
        new_rating.last_rated = datetime.utcnow().isoformat()

        # 写回
        metadata["rating"] = asdict(new_rating)
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

        # 更新注册表
        cap["rating"] = new_rating.overall
        self._save_registry(registry)

        logger.info(f"[CapabilitySharing] Rated: {capability_id} = {new_rating.overall:.3f}")
        return asdict(new_rating)

    def version_control(
        self,
        capability_id: str,
        new_version: Optional[str] = None,
        changelog: str = "",
        breaking: bool = False,
    ) -> dict:
        """
        能力版本管理

        Args:
            capability_id: 能力ID
            new_version: 新版本号（不提供则查询更新）
            changelog: 变更说明
            breaking: 是否破坏性变更

        Returns:
            版本信息
        """
        registry = self._load_registry()
        cap = registry["capabilities"].get(capability_id)
        if not cap:
            raise ValueError(f"Capability not found: {capability_id}")

        cap_dir = Path(cap["path"])
        metadata_path = cap_dir / "metadata.json"
        changelog_path = cap_dir / "CHANGELOG.md"

        if new_version is None:
            # 查询模式：返回当前版本信息
            metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
            return {
                "capability_id": capability_id,
                "current_version": cap.get("version", "1.0.0"),
                "latest_available": metadata.get("version", cap.get("version", "1.0.0")),
                "has_update": False,
                "changelog": changelog_path.read_text(encoding="utf-8") if changelog_path.exists() else "",
            }

        # 发布新版本
        old_version = cap.get("version", "1.0.0")
        cap["version"] = new_version

        # 追加 CHANGELOG
        changelog_entry = self._format_changelog(old_version, new_version, changelog, breaking)
        existing = changelog_path.read_text(encoding="utf-8") if changelog_path.exists() else ""
        changelog_path.write_text(changelog_entry + "\n\n" + existing, encoding="utf-8")

        # 更新 metadata.json
        if metadata_path.exists():
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["version"] = new_version
            metadata["changelog"] = changelog
            metadata["breaking"] = breaking
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

        self._save_registry(registry)
        logger.info(f"[CapabilitySharing] Versioned: {capability_id} {old_version} → {new_version}")

        return {
            "capability_id": capability_id,
            "old_version": old_version,
            "new_version": new_version,
            "breaking": breaking,
            "changelog": changelog,
        }

    # ── 辅助方法 ──────────────────────────────────────────────────────────────

    def _generate_skill_md(
        self,
        cap_id: str,
        name: str,
        description: str,
        tags: list[str],
        trigger_conditions: list[str],
        version: str,
    ) -> str:
        """生成 SKILL.md 内容"""
        return f"""# {name}

> Capability ID: {cap_id} | Version: {version} | Author: M-A3 Self-Evolution System

## 描述

{description}

## 触发条件

{"、".join(trigger_conditions) if trigger_conditions else "由 M-A3 自进化系统在满足条件时自动触发"}

## 标签

{" ".join(f"`{t}`" for t in tags)}

## 使用方法

本能力由 M-A3 自进化系统自动管理，无需手动调用。
当系统检测到匹配触发条件时，将自动激活相应能力。

## 技术信息

- **能力类型**: 自进化模式识别
- **来源**: M-A3 BusinessDrivenEvolution / PatternAggregator
- **创建时间**: {datetime.utcnow().isoformat()}
- **版本**: {version}

## 评分

| 维度 | 分数 |
|------|------|
| 综合 | 暂无评分 |
| 质量 | 暂无评分 |
| 覆盖度 | 暂无评分 |
| 易用性 | 暂无评分 |

---
*本 Skill 由 M-A3 Self-Evolution System 自动生成并持续优化*
"""

    def _format_changelog(
        self,
        old_version: str,
        new_version: str,
        changelog: str,
        breaking: bool,
    ) -> str:
        parts = new_version.split(".")
        if breaking:
            parts[0] = str(int(parts[0]) + 1)
            parts[1] = "0"
            parts[2] = "0"
        elif len(parts) >= 2:
            parts[-1] = str(int(parts[-1]) + 1)
        return (
            f"## {new_version} ({datetime.utcnow().date()})\n"
            f"{'⚠️ BREAKING: ' if breaking else ''}{changelog}\n"
            f"(was {old_version})"
        )

    def _increment_installs(self, capability_id: str):
        registry = self._load_registry()
        cap = registry["capabilities"].get(capability_id)
        if cap:
            cap["installs"] = cap.get("installs", 0) + 1
            self._save_registry(registry)

    def list_capabilities(self, status: Optional[str] = None) -> list[dict]:
        """列出所有能力"""
        registry = self._load_registry()
        caps = registry.get("capabilities", {})
        if status:
            return [c for c in caps.values() if c.get("status") == status]
        return list(caps.values())

    def search_capabilities(self, query: str, tags: Optional[list[str]] = None) -> list[dict]:
        """搜索能力"""
        registry = self._load_registry()
        results = []
        for cap in registry.get("capabilities", {}).values():
            name = cap.get("name", "")
            desc = cap.get("description", "")
            cap_tags = cap.get("tags", [])
            if query.lower() in name.lower() or query.lower() in desc.lower():
                if tags is None or any(t in cap_tags for t in tags):
                    results.append(cap)
        return results

    def get_capability(self, capability_id: str) -> Optional[dict]:
        """获取单个能力详情"""
        registry = self._load_registry()
        return registry.get("capabilities", {}).get(capability_id)
