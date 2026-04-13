"""
evolution/pattern_aggregator.py
================================
模式聚合算法（Pattern Aggregation）

功能：
- 从大量相似问题/失败中提取共同模式
- 评估模式重要性并生成规则候选
- 支持与现有规则去重，避免重复记录

核心算法：
1. 指纹压缩：将问题文本转为语义指纹，过滤噪音
2. 层次聚类：按指纹相似度分组
3. 共同模式提取：分析组内问题的共同关键词/结构
4. 重要性评分：6维评分（频率/严重性/影响/可操作性/新颖性/稳定性）
5. 规则候选生成：基于模式生成可执行的规则建议
"""

from __future__ import annotations

import json
import re
import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional
from collections import defaultdict


# ─────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────

@dataclass
class IssueEntry:
    """单条问题记录"""
    text: str
    fingerprint: str
    timestamp: str
    context: dict = field(default_factory=dict)
    source: str = "unknown"        # 来源：failure/pattern/feedback/manual
    severity: int = 1              # 1-5


@dataclass
class Pattern:
    """识别出的模式"""
    pattern_id: str
    fingerprint: str
    issue_count: int
    sample_texts: list[str]
    common_keywords: list[str]
    root_cause_hypothesis: str     # 根因假设
    suggested_fix: str             # 建议的修复方式
    severity_avg: float
    first_seen: str
    last_seen: str
    importance_score: float = 0.0
    is_novel: bool = True         # 是否与现有规则重复
    related_rule: Optional[str] = None  # 关联的现有规则


@dataclass
class RuleCandidate:
    """规则候选"""
    pattern_id: str
    rule_text: str                # 规则内容（人类可读）
    rule_code: Optional[str]      # 可执行代码（如正则）
    trigger_keywords: list[str]   # 触发关键词
    priority: int                 # 1=最高
    target_file: str              # 写入到哪个文件
    rationale: str                # 生成理由


# ─────────────────────────────────────────────
# 模式聚合器
# ─────────────────────────────────────────────

class PatternAggregator:
    """
    模式聚合器

    工作流程：
    1. 接收问题条目（从 FailureTrigger、PatternTrigger 等收集）
    2. 按指纹分组，合并相似问题
    3. 提取共同模式
    4. 评分并排序
    5. 生成规则候选
    """

    STOPWORDS = {
        "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一",
        "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着",
        "没有", "看", "好", "自己", "这", "那", "但", "还", "什么", "这个",
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "can", "to", "of", "in", "for", "on", "with",
        "at", "by", "from", "as", "or", "and", "but", "if", "not", "so",
    }

    def __init__(
        self,
        rules_dir: str = "evolution/rules",
        failures_dir: str = "evolution/failures",
        min_issues_for_pattern: int = 2,
        max_samples_per_pattern: int = 10,
    ):
        self.rules_dir = Path(rules_dir)
        self.failures_dir = Path(failures_dir)
        self.min_issues_for_pattern = min_issues_for_pattern
        self.max_samples_per_pattern = max_samples_per_pattern
        self._issue_pool: list[IssueEntry] = []
        self._existing_rules: dict[str, str] = {}

    # ── 问题收集 ──

    def add_issue(
        self,
        text: str,
        fingerprint: Optional[str] = None,
        context: Optional[dict] = None,
        source: str = "manual",
        severity: int = 1,
    ):
        """添加一个问题条目"""
        fp = fingerprint or self._generate_fingerprint(text)
        entry = IssueEntry(
            text=text,
            fingerprint=fp,
            timestamp=datetime.utcnow().isoformat(),
            context=context or {},
            source=source,
            severity=severity,
        )
        self._issue_pool.append(entry)

    def add_from_trigger_result(self, trigger_result: dict):
        """从触发器结果中提取问题"""
        ctx = trigger_result.get("context", {})
        if trigger_result.get("trigger_type") == "PATTERN":
            samples = ctx.get("sample_issues", [])
            for s in samples:
                self.add_issue(
                    text=s.get("text", ""),
                    fingerprint=s.get("fingerprint"),
                    context=s.get("ctx", {}),
                    source="pattern_trigger",
                    severity=trigger_result.get("severity", 2),
                )
        elif trigger_result.get("trigger_type") == "FAILURE":
            samples = ctx.get("recent_sample", [])
            for s in samples:
                self.add_issue(
                    text=s.get("msg", ""),
                    context=s.get("ctx", {}),
                    source="failure_trigger",
                    severity=trigger_result.get("severity", 2),
                )

    # ── 核心聚合 ──

    def aggregate_similar_issues(self) -> dict[str, list[IssueEntry]]:
        """
        按指纹聚合相似问题

        Returns:
            fingerprint -> [IssueEntry, ...]
        """
        groups: dict[str, list[IssueEntry]] = defaultdict(list)
        for issue in self._issue_pool:
            groups[issue.fingerprint].append(issue)
        return dict(groups)

    def extract_common_patterns(self) -> list[Pattern]:
        """
        从聚合结果中提取共同模式

        Returns:
            list[Pattern], 按 importance_score 降序
        """
        groups = self.aggregate_similar_issues()
        patterns = []

        for fp, issues in groups.items():
            if len(issues) < self.min_issues_for_pattern:
                continue

            # 提取共同关键词
            all_keywords = []
            for issue in issues:
                kw = self._extract_keywords(issue.text)
                all_keywords.extend(kw)

            keyword_freq = defaultdict(int)
            for kw in all_keywords:
                keyword_freq[kw] += 1

            # 只保留出现次数 >= 50% 问题数的关键词
            threshold = max(2, len(issues) // 2)
            common = sorted(
                [(kw, cnt) for kw, cnt in keyword_freq.items() if cnt >= threshold],
                key=lambda x: x[1],
                reverse=True,
            )[:15]
            common_keywords = [kw for kw, _ in common]

            # 生成根因假设
            root_cause = self._hypothesize_root_cause(issues, common_keywords)

            # 生成修复建议
            suggested_fix = self._generate_fix_suggestion(common_keywords, issues)

            # 时间范围
            timestamps = [i.timestamp for i in issues]
            timestamps.sort()

            pattern = Pattern(
                pattern_id=self._pattern_id_from_fp(fp),
                fingerprint=fp,
                issue_count=len(issues),
                sample_texts=[i.text[:150] for i in issues[: self.max_samples_per_pattern]],
                common_keywords=common_keywords,
                root_cause_hypothesis=root_cause,
                suggested_fix=suggested_fix,
                severity_avg=sum(i.severity for i in issues) / len(issues),
                first_seen=timestamps[0],
                last_seen=timestamps[-1],
            )
            patterns.append(pattern)

        # 评分
        for p in patterns:
            p.importance_score = self.score_pattern_importance(p)

        patterns.sort(key=lambda x: x.importance_score, reverse=True)
        return patterns

    def generate_rule_candidates(self, patterns: list[Pattern]) -> list[RuleCandidate]:
        """从模式生成规则候选"""
        self._load_existing_rules()
        candidates = []

        for pattern in patterns:
            # 检查是否与现有规则重复
            rule_key = self._check_existing_rules(pattern)
            if rule_key:
                pattern.is_novel = False
                pattern.related_rule = rule_key
                continue  # 跳过重复，标记为非新

            keywords = pattern.common_keywords[:5]
            rule_text = self._format_rule_text(pattern, keywords)
            rule_code = self._format_rule_code(pattern, keywords)

            candidates.append(RuleCandidate(
                pattern_id=pattern.pattern_id,
                rule_text=rule_text,
                rule_code=rule_code,
                trigger_keywords=keywords,
                priority=self._calc_priority(pattern),
                target_file=self._select_target_file(pattern),
                rationale=self._format_rationale(pattern),
            ))

        # 按优先级排序
        candidates.sort(key=lambda x: x.priority)
        return candidates

    # ── 评分系统 ──

    def score_pattern_importance(self, pattern: Pattern) -> float:
        """
        六维重要性评分

        权重分配：
        - frequency (0.25): 出现频率
        - severity (0.25): 平均严重程度
        - novelty (0.20): 新颖性（与现有规则的距离）
        - recency (0.15): 最近活跃度
        - stability (0.10): 模式稳定性（持续出现的时间跨度）
        - diversity (0.05): 来源多样性
        """
        # 1. 频率评分 (0-1)
        freq_score = min(1.0, pattern.issue_count / 10)

        # 2. 严重性评分 (0-1)
        sev_score = min(1.0, pattern.severity_avg / 5)

        # 3. 新颖性评分 (0-1)，如果有关联规则则降低
        novelty_score = 0.5 if pattern.is_novel else 0.1

        # 4. 最近活跃度
        try:
            last = datetime.fromisoformat(pattern.last_seen)
            days_ago = (datetime.utcnow() - last).total_seconds() / 86400
            recency_score = max(0, 1 - days_ago / 7)  # 7天内满分，逐渐衰减
        except Exception:
            recency_score = 0.5

        # 5. 稳定性（时间跨度越大越稳定）
        try:
            first = datetime.fromisoformat(pattern.first_seen)
            last_dt = datetime.fromisoformat(pattern.last_seen)
            span_days = max(1, (last_dt - first).total_seconds() / 86400)
            stability_score = min(1.0, pattern.issue_count / max(1, span_days))
        except Exception:
            stability_score = 0.5

        # 6. 多样性（来源多样性）
        sources = set(i.source for i in self._issue_pool if i.fingerprint == pattern.fingerprint)
        diversity_score = min(1.0, len(sources) / 3)

        total = (
            0.25 * freq_score
            + 0.25 * sev_score
            + 0.20 * novelty_score
            + 0.15 * recency_score
            + 0.10 * stability_score
            + 0.05 * diversity_score
        )
        return round(total, 4)

    # ── 辅助方法 ──

    @staticmethod
    def _generate_fingerprint(text: str) -> str:
        words = re.findall(r"[\w]+", text.lower())
        core = sorted(set(w for w in words if len(w) > 2))[:10]
        key = "|".join(core)
        return hashlib.md5(key.encode()).hexdigest()[:12]

    @staticmethod
    def _extract_keywords(text: str) -> list[str]:
        words = re.findall(r"[\w]{2,}", text.lower())
        return [w for w in words if w not in PatternAggregator.STOPWORDS]

    @staticmethod
    def _pattern_id_from_fp(fp: str) -> str:
        return f"PAT-{fp}"

    def _hypothesize_root_cause(self, issues: list[IssueEntry], keywords: list[str]) -> str:
        if not keywords:
            return "未知根因，建议进一步分析"
        kw_str = "、".join(keywords[:5])
        return f"可能与以下因素相关：{kw_str}。需进一步日志分析确认。"

    def _generate_fix_suggestion(self, keywords: list[str], issues: list[IssueEntry]) -> str:
        if not keywords:
            return "建议收集更多样本以识别明确模式"
        kw = keywords[0]
        return f"建议检查与「{kw}」相关的处理逻辑，参考样本中的错误模式进行修复"

    def _format_rule_text(self, pattern: Pattern, keywords: list[str]) -> str:
        kw_str = " / ".join(keywords[:3])
        return (
            f"## {pattern.pattern_id}: {kw_str}\n"
            f"**触发条件**: 问题文本包含 [{kw_str}] 关键词之一\n"
            f"**出现次数**: {pattern.issue_count} 次\n"
            f"**根因假设**: {pattern.root_cause_hypothesis}\n"
            f"**建议操作**: {pattern.suggested_fix}\n"
            f"**首次出现**: {pattern.first_seen} | **最近**: {pattern.last_seen}\n"
        )

    @staticmethod
    def _format_rule_code(pattern: Pattern, keywords: list[str]) -> str:
        kw_list = repr(keywords[:5])
        joined = " or ".join(f"re.search(r'\\b{kw}\\b', text, re.I)" for kw in keywords[:5])
        return (
            f"def match_{pattern.pattern_id}(text: str) -> bool:\n"
            f'    """自动生成规则，匹配: {kw_list}"""\n'
            f"    import re\n"
            f"    return {joined}\n"
        )

    @staticmethod
    def _calc_priority(pattern: Pattern) -> int:
        if pattern.severity_avg >= 4 and pattern.issue_count >= 5:
            return 1
        if pattern.severity_avg >= 3 or pattern.issue_count >= 3:
            return 2
        return 3

    def _select_target_file(self, pattern: Pattern) -> str:
        if pattern.severity_avg >= 3:
            return "rules/CRITICAL.md"
        return "rules/GENERAL.md"

    @staticmethod
    def _format_rationale(pattern: Pattern) -> str:
        return (
            f"模式「{pattern.pattern_id}」由 {pattern.issue_count} 个相似问题聚合而生，"
            f"平均严重程度 {pattern.severity_avg:.1f}/5，新颖性评分 {pattern.importance_score:.2f}。"
            f"建议添加此规则以防止类似问题重复发生。"
        )

    def _load_existing_rules(self):
        """加载现有规则用于去重"""
        self._existing_rules.clear()
        if not self.rules_dir.exists():
            return
        for f in self.rules_dir.glob("*.md"):
            try:
                content = f.read_text(encoding="utf-8")
                self._existing_rules[f.stem] = content
            except Exception:
                pass

    def _check_existing_rules(self, pattern: Pattern) -> Optional[str]:
        """检查模式是否与现有规则重复"""
        for rule_name, rule_content in self._existing_rules.items():
            # 简单关键词重叠检查
            rule_kws = set(self._extract_keywords(rule_content))
            pattern_kws = set(pattern.common_keywords)
            overlap = len(rule_kws & pattern_kws)
            if overlap >= 3:  # 3个以上关键词重叠，认为是重复
                return rule_name
        return None

    # ── 持久化 ──

    def save_patterns(self, patterns: list[Pattern], output_path: str = "evolution/failures/patterns.json"):
        """保存聚合后的模式"""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = [asdict(p) for p in patterns]
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)

    def save_candidates(self, candidates: list[RuleCandidate], output_path: str = "evolution/rules/candidates.json"):
        """保存规则候选"""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = [asdict(c) for c in candidates]
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)
