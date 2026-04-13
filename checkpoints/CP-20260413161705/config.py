"""
evolution/config.py
====================
配置加载器：支持 YAML 和 JSON 格式

默认配置路径：evolution/config.yaml
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
import yaml


@dataclass
class EvolutionConfig:
    """进化系统配置"""
    # 目录
    checkpoints_dir: str = "evolution/checkpoints"
    rules_dir: str = "evolution/rules"
    failures_dir: str = "evolution/failures"
    events_file: str = "evolution/events.jsonl"
    learnings_dir: str = "learnings"

    # 触发器配置
    performance_trigger: dict = field(default_factory=lambda: {
        "enabled": True,
        "threshold_p95": 5.0,
        "threshold_avg10": 3.0,
        "threshold_single": 10.0,
        "window_size": 100,
        "priority": 1,
        "cool_down_seconds": 300,
    })

    failure_trigger: dict = field(default_factory=lambda: {
        "enabled": True,
        "consecutive_threshold": 3,
        "total_threshold": 20,
        "time_window_hours": 24,
        "priority": 1,
        "cool_down_seconds": 300,
    })

    pattern_trigger: dict = field(default_factory=lambda: {
        "enabled": True,
        "min_occurrence": 3,
        "priority": 2,
    })

    feedback_trigger: dict = field(default_factory=lambda: {
        "enabled": True,
        "threshold_negative": 5,
        "threshold_sequence": 3,
        "ratio_threshold": 0.7,
        "window_size": 20,
        "priority": 2,
    })

    schedule_trigger: dict = field(default_factory=lambda: {
        "enabled": True,
        "schedule_type": "DAILY",
        "hour_of_day": 9,
        "day_of_week": 1,
        "priority": 3,
    })

    drift_trigger: dict = field(default_factory=lambda: {
        "enabled": True,
        "response_time_drift_threshold": 0.3,
        "success_rate_drift_threshold": 0.15,
        "token_usage_drift_threshold": 0.4,
        "priority": 2,
    })

    # 模式聚合
    aggregator: dict = field(default_factory=lambda: {
        "min_issues_for_pattern": 2,
        "max_samples_per_pattern": 10,
    })

    # 验证闭环
    verification: dict = field(default_factory=lambda: {
        "stable_threshold": 3,
        "auto_approve_low_risk": True,
    })

    # 进化引擎
    engine: dict = field(default_factory=lambda: {
        "max_changes_per_cycle": 3,
        "force_run_enabled": False,
    })

    @classmethod
    def from_yaml(cls, path: str) -> "EvolutionConfig":
        """从 YAML 文件加载配置"""
        p = Path(path)
        if not p.exists():
            return cls()
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    @classmethod
    def from_json(cls, path: str) -> "EvolutionConfig":
        """从 JSON 文件加载配置"""
        p = Path(path)
        if not p.exists():
            return cls()
        data = json.loads(p.read_text(encoding="utf-8"))
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def to_yaml(self, path: str):
        """保存为 YAML 文件"""
        data = {
            "checkpoints_dir": self.checkpoints_dir,
            "rules_dir": self.rules_dir,
            "failures_dir": self.failures_dir,
            "events_file": self.events_file,
            "learnings_dir": self.learnings_dir,
            "performance_trigger": self.performance_trigger,
            "failure_trigger": self.failure_trigger,
            "pattern_trigger": self.pattern_trigger,
            "feedback_trigger": self.feedback_trigger,
            "schedule_trigger": self.schedule_trigger,
            "drift_trigger": self.drift_trigger,
            "aggregator": self.aggregator,
            "verification": self.verification,
            "engine": self.engine,
        }
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False), encoding="utf-8")
