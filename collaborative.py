"""
evolution/collaborative.py
==========================
Agent 协作进化（Collaborative Evolution）

参考 Accio Work：Agent 之间可以组团队、分工协作

核心功能：
  form_agent_team()       → 组建 Agent 团队并分配角色
  distribute_learning()   → 分布式学习：各 Agent 独立探索后汇总
  sync_evolution_state()  → 同步进化状态：基因/模式/能力库同步
  collective_intelligence() → 集体智慧：多 Agent 投票/加权融合决策

目录结构：
  evolution/teams/                  # Agent 团队配置
  evolution/teams/{team_id}/        # 各团队的独立目录
      team.json                    # 团队配置
      members.json                 # 成员信息
      shared_state.json             # 共享状态
      votes.jsonl                   # 决策投票记录
"""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, Any
from collections import defaultdict
import logging

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────

@dataclass
class AgentMember:
    """Agent 团队成员"""
    agent_id: str
    name: str
    role: str                          # "explorer" | "verifier" | "synthesizer" | "critic"
    specialization: list[str]           # 专长领域
    authority_level: int = 1           # 1-3：1=观察，2=建议，3=决策
    state_snapshot: dict = field(default_factory=dict)
    last_sync: Optional[str] = None


@dataclass
class TeamDecision:
    """团队决策"""
    decision_id: str
    team_id: str
    topic: str
    votes: dict[str, str]              # agent_id → vote
    weights: dict[str, float]          # agent_id → authority weight
    result: str                        # "adopted" | "rejected" | "deferred"
    reasoning: str
    timestamp: str = ""
    participants: list[str] = field(default_factory=list)


@dataclass
class SharedEvolutionState:
    """共享进化状态"""
    team_id: str
    shared_genes: dict = field(default_factory=dict)    # 基因快照
    shared_patterns: list = field(default_factory=list)  # 共享模式
    shared_rules: list = field(default_factory=list)     # 共享规则
    local_insights: dict = field(default_factory=dict)  # 各 Agent 的本地洞察
    last_sync: str = ""


# ─────────────────────────────────────────────
# Agent 协作进化管理器
# ─────────────────────────────────────────────

class CollaborativeEvolution:
    """
    Agent 协作进化管理器

    使用示例：
        collab = CollaborativeEvolution(base_dir="evolution/teams")

        # 组建团队
        team_id = collab.form_agent_team(
            name="业务优化团队",
            members=[
                {"agent_id": "A1", "name": "Explorer-A", "role": "explorer", "specialization": ["data_analysis"]},
                {"agent_id": "A2", "name": "Verifier-B", "role": "verifier", "specialization": ["validation"]},
            ]
        )

        # 分布式学习
        results = collab.distribute_learning(team_id, task="优化数据库查询策略")

        # 集体智慧决策
        decision = collab.collective_intelligence(
            team_id,
            topic="是否采用新的缓存策略",
            options=["adopt", "reject", "defer"],
            context={"current_hit_rate": 0.65, "target": 0.85},
        )

        # 同步进化状态
        sync_result = collab.sync_evolution_state(team_id)
    """

    def __init__(
        self,
        base_dir: str = "evolution/teams",
        local_genes_path: str = "evolution/genes.json",
        local_patterns_path: str = "evolution/patterns.json",
    ):
        self.base_dir = Path(base_dir)
        self.local_genes_path = Path(local_genes_path)
        self.local_patterns_path = Path(local_patterns_path)

        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._team_index_path = self.base_dir / "team_index.json"
        self._init_team_index()

    def _init_team_index(self):
        """初始化团队索引"""
        if not self._team_index_path.exists():
            self._save_team_index({"teams": {}, "last_updated": datetime.utcnow().isoformat()})

    def _load_team_index(self) -> dict:
        if self._team_index_path.exists():
            return json.loads(self._team_index_path.read_text(encoding="utf-8"))
        return {"teams": {}}

    def _save_team_index(self, data: dict):
        data["last_updated"] = datetime.utcnow().isoformat()
        self._team_index_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── 核心方法 ───────────────────────────────────────────────────────────────

    def form_agent_team(
        self,
        name: str,
        members: list[dict],
        strategy: str = "hierarchical",
    ) -> str:
        """
        组建 Agent 团队

        Args:
            name: 团队名称
            members: 成员列表
                [{
                    "agent_id": str,
                    "name": str,
                    "role": "explorer" | "verifier" | "synthesizer" | "critic",
                    "specialization": list[str],
                    "authority_level": int (1-3),
                }]
            strategy: 协作策略
                - "hierarchical": 层级式（synthesizer 汇总 explorer）
                - "democratic": 民主式（平等投票）
                - "specialized": 专业分工式

        Returns:
            team_id
        """
        team_id = f"TEAM-{name[:8].upper().replace(' ', '-')}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        team_dir = self.base_dir / team_id
        team_dir.mkdir(parents=True, exist_ok=True)

        # 构建成员对象
        agent_members = []
        for m in members:
            agent_members.append(AgentMember(
                agent_id=m["agent_id"],
                name=m["name"],
                role=m.get("role", "explorer"),
                specialization=m.get("specialization", []),
                authority_level=m.get("authority_level", 1),
            ))

        # 角色配置
        role_config = {
            "explorer": {"duty": "探索新模式/策略", "authority": 1},
            "verifier": {"duty": "验证假设/策略可行性", "authority": 2},
            "synthesizer": {"duty": "综合多方信息做决策", "authority": 3},
            "critic": {"duty": "批评分析，识别风险", "authority": 2},
        }

        team_data = {
            "team_id": team_id,
            "name": name,
            "strategy": strategy,
            "created_at": datetime.utcnow().isoformat(),
            "members": [asdict(m) for m in agent_members],
            "status": "active",
            "role_config": role_config,
            "decision_history": [],
            "performance_metrics": {
                "decisions_adopted": 0,
                "decisions_rejected": 0,
                "total_votes": 0,
            },
        }

        # 共享状态
        shared_state = SharedEvolutionState(team_id=team_id)
        (team_dir / "team.json").write_text(
            json.dumps(team_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (team_dir / "members.json").write_text(
            json.dumps([asdict(m) for m in agent_members], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (team_dir / "shared_state.json").write_text(
            json.dumps(asdict(shared_state), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (team_dir / "votes.jsonl").write_text("", encoding="utf-8")

        # 更新索引
        index = self._load_team_index()
        index["teams"][team_id] = {
            "name": name,
            "strategy": strategy,
            "member_count": len(agent_members),
            "path": str(team_dir),
            "status": "active",
        }
        self._save_team_index(index)

        logger.info(f"[CollaborativeEvolution] Team formed: {team_id} ({len(agent_members)} members)")
        return team_id

    def distribute_learning(
        self,
        team_id: str,
        task: str,
        exploration_epochs: int = 3,
    ) -> dict:
        """
        分布式学习：各 Agent 独立探索同一任务，最终汇总发现

        Args:
            team_id: 团队ID
            task: 学习任务描述
            exploration_epochs: 探索轮数

        Returns:
            汇总后的学习结果
        """
        team_dir = self.base_dir / team_id
        if not team_dir.exists():
            raise ValueError(f"Team not found: {team_id}")

        team_data = json.loads((team_dir / "team.json").read_text(encoding="utf-8"))
        members = [AgentMember(**m) for m in team_data["members"]]

        # 按角色分组
        explorers = [m for m in members if m.role == "explorer"]
        verifiers = [m for m in members if m.role == "verifier"]

        if not explorers:
            raise ValueError("Team must have at least one explorer agent")

        # 阶段1：探索阶段（各 Agent 独立）
        explorations: list[dict] = []
        for epoch in range(exploration_epochs):
            epoch_results: list[dict] = []
            for agent in explorers:
                # 模拟独立探索（实际调用 Agent API）
                finding = self._simulate_agent_exploration(agent, task, epoch)
                epoch_results.append(finding)
                explorations.append(finding)
                self._update_member_state(team_dir, agent.agent_id, finding)

            # 阶段2：验证阶段
            if verifiers:
                for agent in verifiers:
                    verification = self._simulate_agent_verification(agent, epoch_results)
                    explorations.append(verification)
                    self._update_member_state(team_dir, agent.agent_id, verification)

        # 阶段3：综合阶段
        synthesis = self._synthesize_findings(team_id, explorations)

        # 更新团队状态
        team_data = json.loads((team_dir / "team.json").read_text(encoding="utf-8"))
        team_data["performance_metrics"]["total_votes"] += len(explorations)
        (team_dir / "team.json").write_text(
            json.dumps(team_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        return {
            "team_id": team_id,
            "task": task,
            "epochs": exploration_epochs,
            "findings_count": len(explorations),
            "synthesis": synthesis,
            "explorers": [m.agent_id for m in explorers],
            "completed_at": datetime.utcnow().isoformat(),
        }

    def sync_evolution_state(self, team_id: str) -> dict:
        """
        同步进化状态：团队共享基因快照、模式和规则

        Args:
            team_id: 团队ID

        Returns:
            同步结果
        """
        team_dir = self.base_dir / team_id
        if not team_dir.exists():
            raise ValueError(f"Team not found: {team_id}")

        # 读取本地基因和模式
        genes = {}
        if self.local_genes_path.exists():
            genes = json.loads(self.local_genes_path.read_text(encoding="utf-8"))

        patterns = []
        if self.local_patterns_path.exists():
            patterns = json.loads(self.local_patterns_path.read_text(encoding="utf-8"))

        rules = []
        rules_dir = Path("evolution/rules")
        if rules_dir.exists():
            for md_file in rules_dir.glob("*.md"):
                rules.append({
                    "name": md_file.stem,
                    "path": str(md_file),
                    "content_hash": hashlib.md5(md_file.read_bytes()).hexdigest()[:8],
                })

        # 构建共享状态
        shared = SharedEvolutionState(
            team_id=team_id,
            shared_genes={"current_value": genes.get("genes", {})},
            shared_patterns=patterns[-20:] if patterns else [],  # 最近20条
            shared_rules=rules[-20:] if rules else [],
            last_sync=datetime.utcnow().isoformat(),
        )

        # 写回团队共享状态
        (team_dir / "shared_state.json").write_text(
            json.dumps(asdict(shared), ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # 更新成员同步时间
        team_data = json.loads((team_dir / "team.json").read_text(encoding="utf-8"))
        for m in team_data["members"]:
            m["last_sync"] = datetime.utcnow().isoformat()
        (team_dir / "team.json").write_text(
            json.dumps(team_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        logger.info(f"[CollaborativeEvolution] State synced: {team_id}")
        return {
            "team_id": team_id,
            "genes_count": len(shared.shared_genes.get("current_value", {})),
            "patterns_count": len(shared.shared_patterns),
            "rules_count": len(shared.shared_rules),
            "synced_at": shared.last_sync,
        }

    def collective_intelligence(
        self,
        team_id: str,
        topic: str,
        options: list[str],
        context: Optional[dict] = None,
        threshold: float = 0.6,
    ) -> TeamDecision:
        """
        集体智慧：多 Agent 投票/加权融合决策

        Args:
            team_id: 团队ID
            topic: 决策主题
            options: 可选决策列表
            context: 决策上下文
            threshold: 通过阈值（默认 60%）

        Returns:
            TeamDecision（含投票结果和决策）
        """
        team_dir = self.base_dir / team_id
        if not team_dir.exists():
            raise ValueError(f"Team not found: {team_id}")

        team_data = json.loads((team_dir / "team.json").read_text(encoding="utf-8"))
        members = [AgentMember(**m) for m in team_data["members"]]

        # 投票
        votes: dict[str, str] = {}
        weights: dict[str, float] = {}

        for agent in members:
            if agent.authority_level < 1:
                continue
            vote = self._agent_vote(agent, topic, options, context or {})
            votes[agent.agent_id] = vote
            # 权重 = authority_level * 专长匹配度（简化：直接用 authority_level）
            weights[agent.agent_id] = float(agent.authority_level)

        # 加权计票
        option_scores: dict[str, float] = defaultdict(float)
        total_weight = sum(weights.values())
        for agent_id, vote in votes.items():
            w = weights[agent_id] / total_weight
            option_scores[vote] += w

        # 决定结果
        best_option = max(option_scores, key=option_scores.get)
        best_score = option_scores.get(best_option, 0)

        if best_score >= threshold:
            result = "adopted"
            team_data["performance_metrics"]["decisions_adopted"] += 1
        elif best_score >= 0.3:
            result = "deferred"
        else:
            result = "rejected"
            team_data["performance_metrics"]["decisions_rejected"] += 1

        team_data["performance_metrics"]["total_votes"] += len(members)
        (team_dir / "team.json").write_text(
            json.dumps(team_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # 生成推理说明
        reasoning = self._generate_reasoning(topic, options, votes, weights, option_scores, result)

        decision = TeamDecision(
            decision_id=f"DEC-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}",
            team_id=team_id,
            topic=topic,
            votes=votes,
            weights=weights,
            result=result,
            reasoning=reasoning,
            timestamp=datetime.utcnow().isoformat(),
            participants=list(votes.keys()),
        )

        # 记录投票
        with open(team_dir / "votes.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(decision), ensure_ascii=False) + "\n")

        logger.info(f"[CollaborativeEvolution] Decision: {team_id} {topic} → {result} ({best_score:.2f})")
        return decision

    # ── 辅助方法 ──────────────────────────────────────────────────────────────

    def _agent_vote(
        self,
        agent: AgentMember,
        topic: str,
        options: list[str],
        context: dict,
    ) -> str:
        """
        模拟 Agent 投票（实际系统可替换为真实 Agent API 调用）

        策略：
        - synthesizer：偏好中间选项（synthetic/balanced）
        - critic：偏好保守选项（reject/defer）
        - explorer：偏好激进选项（adopt/new）
        - verifier：偏好验证过的选项
        """
        import random

        topic_lower = topic.lower()
        ctx_indicators = {
            "positive": sum(1 for v in context.values() if isinstance(v, (int, float)) and v > 0.7),
            "negative": sum(1 for v in context.values() if isinstance(v, (int, float)) and v < 0.4),
        }

        if agent.role == "synthesizer":
            # 综合型：倾向 deferred（等待更多信息）
            return "defer" if random.random() < 0.5 else options[0] if options else "adopt"

        elif agent.role == "critic":
            # 批评型：倾向保守
            return "reject" if ctx_indicators["negative"] > 0 else "defer"

        elif agent.role == "explorer":
            # 探索型：倾向激进
            return "adopt" if ctx_indicators["positive"] > 0 else options[0] if options else "adopt"

        elif agent.role == "verifier":
            # 验证型：倾向通过已有证据决策
            return options[0] if options else "adopt"

        return random.choice(options) if options else "defer"

    def _simulate_agent_exploration(
        self,
        agent: AgentMember,
        task: str,
        epoch: int,
    ) -> dict:
        """模拟 Agent 探索（实际可替换为真实 Agent API）"""
        return {
            "agent_id": agent.agent_id,
            "role": agent.role,
            "epoch": epoch,
            "finding": f"[{agent.name}] Epoch {epoch} 探索: {task[:30]}...",
            "confidence": 0.5 + (epoch * 0.1),
            "patterns_found": [f"pattern_{epoch}_{i}" for i in range(2)],
            "timestamp": datetime.utcnow().isoformat(),
        }

    def _simulate_agent_verification(
        self,
        agent: AgentMember,
        epoch_results: list[dict],
    ) -> dict:
        """模拟 Agent 验证（实际可替换为真实 Agent API）"""
        valid_count = sum(1 for r in epoch_results if r.get("confidence", 0) > 0.5)
        return {
            "agent_id": agent.agent_id,
            "role": agent.role,
            "verified": valid_count,
            "total": len(epoch_results),
            "confidence": valid_count / max(len(epoch_results), 1),
            "timestamp": datetime.utcnow().isoformat(),
        }

    def _synthesize_findings(self, team_id: str, findings: list[dict]) -> dict:
        """综合多 Agent 探索结果"""
        high_confidence = [f for f in findings if f.get("confidence", 0) >= 0.6]
        patterns = []
        for f in findings:
            patterns.extend(f.get("patterns_found", []))

        return {
            "total_findings": len(findings),
            "high_confidence_count": len(high_confidence),
            "unique_patterns": len(set(patterns)),
            "synthesis_score": len(high_confidence) / max(len(findings), 1),
            "recommended_actions": [
                f"采纳 {len(high_confidence)} 条高置信度发现",
                f"进一步验证 {len(findings) - len(high_confidence)} 条待确认发现",
            ],
            "synthesized_at": datetime.utcnow().isoformat(),
        }

    def _update_member_state(self, team_dir: Path, agent_id: str, state_update: dict):
        """更新成员状态快照"""
        members_path = team_dir / "members.json"
        members = json.loads(members_path.read_text(encoding="utf-8"))
        for m in members:
            if m["agent_id"] == agent_id:
                m["state_snapshot"] = {**m.get("state_snapshot", {}), **state_update}
                m["last_sync"] = datetime.utcnow().isoformat()
        members_path.write_text(json.dumps(members, ensure_ascii=False, indent=2), encoding="utf-8")

    def _generate_reasoning(
        self,
        topic: str,
        options: list[str],
        votes: dict[str, str],
        weights: dict[str, float],
        scores: dict[str, float],
        result: str,
    ) -> str:
        """生成决策推理说明"""
        lines = [
            f"决策主题：{topic}",
            f"投票分布：{dict(scores)}",
            f"加权得分最高：{max(scores, key=scores.get)} ({max(scores.values()):.2%})",
            f"最终结果：{result}",
            f"参与 Agent：{len(votes)}",
        ]
        return "\n".join(lines)

    def list_teams(self, status: Optional[str] = None) -> list[dict]:
        """列出所有团队"""
        index = self._load_team_index()
        teams = index.get("teams", {})
        if status:
            return [t for t in teams.values() if t.get("status") == status]
        return list(teams.values())

    def get_team_status(self, team_id: str) -> Optional[dict]:
        """获取团队状态"""
        team_dir = self.base_dir / team_id
        if not team_dir.exists():
            return None
        team_data = json.loads((team_dir / "team.json").read_text(encoding="utf-8"))
        shared = json.loads((team_dir / "shared_state.json").read_text(encoding="utf-8"))
        return {
            **team_data,
            "shared_state_summary": {
                "genes_count": len(shared.get("shared_genes", {}).get("current_value", {})),
                "patterns_count": len(shared.get("shared_patterns", [])),
                "rules_count": len(shared.get("shared_rules", [])),
                "last_sync": shared.get("last_sync"),
            },
        }

    def dissolve_team(self, team_id: str) -> dict:
        """解散团队（保留历史记录）"""
        index = self._load_team_index()
        if team_id in index["teams"]:
            index["teams"][team_id]["status"] = "dissolved"
            self._save_team_index(index)
            logger.info(f"[CollaborativeEvolution] Team dissolved: {team_id}")
        return {"team_id": team_id, "status": "dissolved"}
