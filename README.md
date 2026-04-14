# M-A3 Self-Evolution System

> 构建时间：2026-04-13 | 版本：1.0.0 | 状态：✅ 完整实现

---

## 一、系统概述

M-A3 自进化系统（M-A3 Self-Evolution System）是 M-A3 的自我改进引擎，通过自动触发、模式聚合、验证闭环和 GEP 进化协议实现持续自我优化。

### 四大差距填补

| 差距 | 原状态 | 解决方案 |
|------|--------|----------|
| ❌ 无自动触发器 | 依赖手动记录 | 六类自动触发器（triggers.py）|
| ❌ 无模式聚合算法 | 重复问题反复记录 | 模式聚合算法（pattern_aggregator.py）|
| ❌ 无验证闭环 | 自我修改无回滚 | 验证闭环（verification.py）|
| ❌ 无 GEP 进化协议 | 无进化资产结构 | GEP 协议层（genes.json/capsules.json/events.jsonl）|

---

## 二、目录结构

```
evolution/
├── __init__.py              # 包导出
├── triggers.py              # 六类自动触发器（P0）
├── pattern_aggregator.py    # 模式聚合算法（P1）
├── verification.py          # 验证闭环（P0）
├── engine.py                # 进化引擎（P2）
├── config.py                # 配置加载器
├── config.yaml              # 配置文件（可调参数）
├── genes.json               # GEP 基因组定义
├── capsules.json            # 能力胶囊注册表
├── events.jsonl             # 进化事件日志（append only）
├── README.md                # 本文件
├── checkpoints/             # 检查点存储（自动创建）
│   ├── index.json           # 检查点索引
│   └── CP-YYYYMMDDHHMMSS/   # 各检查点的快照副本
├── rules/                   # 生成的规则（进化产出）
│   ├── GENERAL.md           # 一般规则
│   └── CRITICAL.md          # 高优先级规则
├── failures/                # 失败记录
│   └── failure_EVT-xxx.json # 各次失败的详情
└── capsules/                # 能力胶囊存储（自动创建）
```

---

## 三、快速开始

### 初始化引擎

```python
from evolution import EvolutionEngine, EvolutionConfig

# 方式1：从配置文件加载
config = EvolutionConfig.from_yaml("evolution/config.yaml")
engine = EvolutionEngine()

# 方式2：使用自定义配置
engine = EvolutionEngine(
    checkpoints_dir="evolution/checkpoints",
    rules_dir="evolution/rules",
    failures_dir="evolution/failures",
    events_file="evolution/events.jsonl",
)
engine.init_triggers(config.__dict__ if hasattr(config, '__dict__') else None)
```

### 执行进化周期

```python
# 自动检测触发器并执行完整进化
cycle = engine.run_evolution_cycle()
print(f"Cycle: {cycle.cycle_id}, Status: {cycle.final_status}")
print(f"Patterns: {cycle.patterns_found}, Applied: {cycle.changes_applied}")
```

### 手动记录数据

```python
# 记录响应时间（触发性能检查）
engine.record_response_time(6.5)  # 超过阈值 → 触发 PerformanceTrigger

# 记录失败（触发失败检查）
engine.record_failure(
    failure_type="API_TIMEOUT",
    error_msg="Request timeout after 30s",
    context={"endpoint": "/api/data", "method": "GET"}
)

# 记录问题（触发模式检查）
engine.record_issue(
    "数据库连接失败，连接池耗尽",
    context={"db": "postgres", "pool_size": 10}
)

# 记录反馈（触发反馈检查）
engine.record_feedback(
    is_negative=True,
    feedback_text="回答不准确，请重新分析",
    context={"query": "xxx", "rating": 2}
)

# 捕获基线（供漂移检测使用）
engine.capture_baseline(
    avg_response_time=2.1,
    success_rate=0.95,
    avg_token_usage=1500,
    top_behaviors=["code_review", "debug", "refactor"]
)
```

### 获取进化摘要

```python
summary = engine.get_cycle_summary()
print(f"Total cycles: {summary['total_cycles']}")
print(f"Total rollbacks: {summary['total_rollbacks']}")
print(f"Total promotions: {summary['total_promotions']}")
```

---

## 四、六类触发器详解

### 1. PerformanceTrigger（性能触发器）⚡ P0

**触发条件**（满足任一）：
- P95 响应时间超过 `threshold_p95`
- 最近 10 次平均响应时间超过 `threshold_avg10`
- 单次响应时间超过 `threshold_single`

```python
from evolution.triggers import PerformanceTrigger

trigger = PerformanceTrigger(
    threshold_p95=5.0,      # 秒
    threshold_avg10=3.0,    # 秒
    threshold_single=10.0,  # 秒
    window_size=100,
)
trigger.record_response_time(6.5)
result = trigger.check()
# result.triggered == True → 性能下降
```

### 2. FailureTrigger（失败触发器）⚡ P0

**触发条件**（满足任一）：
- 连续失败 ≥ `consecutive_threshold` 次
- 累计失败 ≥ `total_threshold` 次

```python
from evolution.triggers import FailureTrigger

trigger = FailureTrigger(
    consecutive_threshold=3,
    total_threshold=20,
)
trigger.record_failure("API_ERROR", "Connection refused", {"host": "api.example.com"})
result = trigger.check()
```

### 3. PatternTrigger（模式触发器）🔄 P1

**触发条件**：相同指纹问题出现 ≥ `min_occurrence` 次

```python
from evolution.triggers import PatternTrigger

trigger = PatternTrigger(min_occurrence=3)
trigger.record_issue("数据库连接超时，连接池满", {"db": "mysql"})
trigger.record_issue("MySQL连接超时", {"db": "mysql"})  # 相同指纹
result = trigger.check()
```

### 4. FeedbackTrigger（反馈触发器）🔄 P1

**触发条件**（满足任一）：
- 累计负反馈 ≥ `threshold_negative`
- 连续负反馈 ≥ `threshold_sequence`
- 负反馈占比 > `ratio_threshold`

```python
from evolution.triggers import FeedbackTrigger

trigger = FeedbackTrigger(
    threshold_negative=5,
    threshold_sequence=3,
    ratio_threshold=0.7,
)
trigger.record_feedback(is_negative=True, feedback_text="回答错误")
result = trigger.check()
```

### 5. ScheduleTrigger（定时触发器）⏰ P1

**触发条件**：到达预设时间（每小时/每天/每周）

```python
from evolution.triggers import ScheduleTrigger, ScheduleType

trigger = ScheduleTrigger(
    schedule_type=ScheduleType.DAILY,
    hour_of_day=9,  # UTC 9:00
)
result = trigger.check()
# 每天 9:00 UTC 自动触发 → 生成健康报告
```

### 6. DriftTrigger（漂移触发器）📊 P1

**触发条件**：当前行为/性能偏离基线超过阈值

```python
from evolution.triggers import DriftTrigger

trigger = DriftTrigger(
    response_time_drift_threshold=0.3,   # 30%
    success_rate_drift_threshold=0.15,   # 15%
)
# 先捕获基线
trigger.capture_baseline(avg_response_time=2.1, success_rate=0.95, avg_token_usage=1500)
# 运行中记录当前
trigger.record_current(avg_response_time=3.5, success_rate=0.75, avg_token_usage=2000)
result = trigger.check()
```

---

## 五、模式聚合算法

### 算法流程

```
问题输入 → 指纹生成 → 层次聚类 → 共同关键词提取 → 评分排序 → 规则生成 → 去重检查
```

### 六维重要性评分

| 维度 | 权重 | 说明 |
|------|------|------|
| Frequency | 25% | 出现频率（越多越重要）|
| Severity | 25% | 平均严重程度（越严重越优先）|
| Novelty | 20% | 新颖性（与现有规则距离）|
| Recency | 15% | 最近活跃度（越近越重要）|
| Stability | 10% | 稳定性（持续时间跨度）|
| Diversity | 5% | 来源多样性（多来源更可靠）|

```python
from evolution.pattern_aggregator import PatternAggregator

aggregator = PatternAggregator(
    rules_dir="evolution/rules",
    failures_dir="evolution/failures",
    min_issues_for_pattern=2,
)

# 添加问题
aggregator.add_issue("数据库连接超时", source="failure_trigger", severity=4)
aggregator.add_issue("MySQL连接失败", source="pattern_trigger", severity=3)

# 提取模式
patterns = aggregator.extract_common_patterns()
for p in patterns:
    print(f"{p.pattern_id}: score={p.importance_score:.2f}, count={p.issue_count}")

# 生成规则候选
candidates = aggregator.generate_rule_candidates(patterns)
for c in candidates:
    print(f"  → {c.rule_text[:60]}...")
```

---

## 六、验证闭环

### 三阶段验证流程

```
Stage 1: 静态检查
  ├─ JSON 语法验证
  ├─ YAML 结构验证
  └─ Python 语法编译检查

Stage 2: 回归测试
  └─ 运行 pytest tests/（如存在）

Stage 3: 指标验证
  ├─ 风险等级评估
  └─ 变更范围检查
```

### 检查点管理

```python
from evolution.verification import VerificationLoop, ProposedChange, ChangeType

vl = VerificationLoop(
    checkpoints_dir="evolution/checkpoints",
    failures_dir="evolution/failures",
)

# 创建检查点
cp = vl.create_checkpoint(
    description="Before adding new rule",
    metrics={"success_rate": 0.95, "avg_response_time": 2.1},
    change_description="Add rule for DB timeout handling",
)

# 应用变更
change = ProposedChange(
    change_id="RULE-DB-001",
    change_type=ChangeType.RULE_ADD,
    description="处理数据库超时问题",
    target_files=["evolution/rules/DB-TIMEOUT.md"],
    proposed_content={"evolution/rules/DB-TIMEOUT.md": "# DB Timeout Rule\n..."},
    rollback_from_checkpoint=cp.checkpoint_id,
    risk_level="medium",
)
vl.apply_change(change)

# 验证
result = vl.validate_change(change, cp)
if result.status.value == "passed":
    vl.promote_if_passed(result, "# DB Timeout Rule\n...")
elif result.status.value in ("failed", "partial"):
    vl.rollback_if_failed(result, change, reason="Tests failed")
```

---

## 七、GEP 进化协议

### 基因组（genes.json）

可进化参数，驱动系统自适应调整：

| 基因 ID | 描述 | 当前值 | 可进化 |
|---------|------|--------|--------|
| G-RT-001 | 响应时间阈值 | 5.0s | ✅ |
| G-FS-001 | 失败敏感度 | 3次 | ✅ |
| G-PR-001 | 模式识别阈值 | 3次 | ✅ |
| G-FW-001 | 反馈权重 | 0.7 | ✅ |
| G-DT-001 | 漂移容忍度 | 0.3 | ✅ |
| G-CR-001 | 检查点保留数 | 10个 | ❌ |
| G-CD-001 | 进化冷却时间 | 300s | ✅ |

### 能力胶囊（capsules.json）

晋升后的规则封装为能力胶囊，支持版本化管理：

```python
# 胶囊自动从晋升的规则生成
# 存储在 capsules.json 的 capsules 字段中
{
  "capsule_id": "CAP-RULE-001",
  "name": "数据库超时处理",
  "version": "1.0.0",
  "stability": "stable",
  "verified": true
}
```

### 进化事件（events.jsonl）

Append-only 事件日志，每条记录一个进化事件：

```json
{"event_id":"EVT-20260413093000","timestamp":"2026-04-13T09:30:00","phase":"TRIGGER_CHECK","trigger_type":"PERFORMANCE","trigger_reason":"P95=6.5s > 5.0s","severity":3,"pattern_id":null,"change_id":null,"verification_status":null,"rollback_performed":false,"promoted":false}
```

---

## 八、与现有系统集成

### 读取 SOUL.md（M-A3 身份）

```python
soul_path = Path("基础设定/SOUL.md")
soul_content = soul_path.read_text(encoding="utf-8")
# → 获取 M-A3 的性格特点、进化原则等
```

### 读取 MEMORY.md（工作记忆）

```python
memory_path = Path("MEMORY.md")
memory_content = memory_path.read_text(encoding="utf-8")
# → 获取当前任务上下文、关键决定等
```

### 写入 rules/（生成的规则）

```python
rule_file = Path("evolution/rules") / f"{pattern_id}.md"
rule_file.write_text(rule_content, encoding="utf-8")
```

### 写入 failures/（失败记录）

```python
failure_file = Path("evolution/failures") / f"failure_{event_id}.json"
failure_file.write_text(json.dumps(failure_record, ensure_ascii=False, indent=2))
```

### 写入 learnings/（学习文档）

```python
learning_file = Path("learnings") / f"learning_{date}.md"
# 格式：## 标题 | 时间 | 来源 | 内容摘要
```

---

## 九、配置调整

所有触发器参数都在 `evolution/config.yaml` 中配置，无需修改代码：

```yaml
# 性能触发器：更敏感
performance_trigger:
  threshold_p95: 2.0    # 从 5.0 改为 2.0
  priority: 1

# 模式触发器：更宽松
pattern_trigger:
  min_occurrence: 5     # 从 3 改为 5

# 定时触发器：每天凌晨 2 点
schedule_trigger:
  hour_of_day: 2
```

重新加载配置：

```python
config = EvolutionConfig.from_yaml("evolution/config.yaml")
engine.init_triggers(config.performance_trigger)  # 只更新性能触发器
```

---

## 十、输出产物

| 产物 | 路径 | 说明 |
|------|------|------|
| 生成的规则 | `evolution/rules/*.md` | 自动生成的行为规则 |
| 失败记录 | `evolution/failures/*.json` | 每次失败的详细信息 |
| 检查点 | `evolution/checkpoints/CP-*/` | 可回滚的系统快照 |
| 进化事件 | `evolution/events.jsonl` | 所有进化事件的 Append-only 日志 |
| 模式快照 | `evolution/failures/patterns.json` | 当前识别的所有模式 |
| 规则候选 | `evolution/rules/candidates.json` | 待审核的规则候选 |

---

## 十一、运行状态查看

```python
# 查看进化摘要
summary = engine.get_cycle_summary()

# 查看最近的进化事件
events = engine.get_events(limit=20)
for e in events[-5:]:
    print(f"{e['timestamp']} | {e['phase']} | severity={e['severity']}")

# 查看当前周期状态
cycle = engine.get_current_cycle()
if cycle:
    print(f"Cycle {cycle.cycle_id}: {cycle.phase.name}, {cycle.final_status}")
```

---

## 十二、业务驱动进化（Business-Driven Evolution）

> 参考 Accio Work：根据生意进展迭代商业判断能力
> 模块：`evolution/engine.py` → `BusinessDrivenEvolution`

### 初始化

```python
from evolution import EvolutionEngine, BusinessDrivenEvolution

engine = EvolutionEngine()
biz = BusinessDrivenEvolution(engine)
```

### 追踪业务指标

```python
result = biz.track_business_metrics({
    "task_completion_rate": 0.87,       # 任务完成率
    "avg_response_time": 2.8,            # 平均响应时间（秒）
    "failure_rate": 0.04,                 # 失败率
    "pattern_recognition_rate": 0.72,     # 模式识别命中率
    "decision_success_rate": 0.78,        # 决策成功率
    "user_satisfaction": 4.3,             # 用户满意度（1-5）
})
# 返回：窗口均值、是否需要调整
```

### 评估决策质量

```python
eval_result = biz.evaluate_decision_quality(
    decision_id="DEC-20260413-001",
    decision_context={"trigger_type": "PerformanceTrigger", "task": "优化查询"},
    outcome={
        "success": True,
        "task_completed": True,
        "response_time": 2.1,
        "user_rating": 4.5,
        "pattern_promoted": False,
    }
)
print(f"Quality: {eval_result['quality_score']:.2f}")
# Quality: 0.83
```

### 自动调整策略参数

```python
adjustments = biz.adjust_strategy()
# 基于指标窗口，自动调整 genes.json 中的进化基因
# 例如：failure_rate 偏高 → 降低 failure_sensitivity_gene 阈值
```

### 晋升成功模式

```python
promoted = biz.promote_successful_patterns()
# 分析最近20条决策，按触发类型聚合
# 平均质量 ≥ 0.75 的模式 → 晋升为能力胶囊（CAP-xxx）
```

### 业务摘要

```python
summary = biz.get_business_summary()
print(f"Window: {summary['metrics_window_size']}, "
      f"Decisions: {summary['decisions_tracked']}, "
      f"Avg Quality: {summary['recent_decision_avg_quality']}")
```

---

## 十三、能力共享机制（Capability Sharing）

> 参考 Accio Work：Skill 分享扩充能力库
> 模块：`evolution/sharing.py` → `CapabilitySharing`

### 目录结构

```
evolution/capabilities/           # 能力包存储
evolution/capabilities/{id}/
    SKILL.md                       # 标准 Skill 定义
    metadata.json                  # 元数据（版本/评分/标签）
    CHANGELOG.md                   # 版本变更记录
    implementation/                # 可选：实现代码
evolution/capabilities_registry.json  # 能力索引
```

### 导出能力

```python
from evolution import CapabilitySharing, EvolutionEngine

sharing = CapabilitySharing()
pkg = sharing.export_capability(
    capsule_id="CAP-PATTERN-001",
    name="数据库连接池优化",
    description="自动识别连接池耗尽并建议扩展策略",
    tags=["database", "performance", "optimization"],
    trigger_conditions=["数据库查询超时", "连接池耗尽"],
    implementation_type="pattern",
)
print(f"Exported: {pkg.capability_id}")
# 产出：evolution/capabilities/SKL-TERN-001/ 下有 SKILL.md + metadata.json
```

### 导入能力

```python
# 从本地包导入
result = sharing.import_capability("evolution/capabilities/SKL-TERN-001", import_type="local")

# 从社区导入
result = sharing.import_capability("community-capability-id", import_type="community")
```

### 评分能力

```python
rating = sharing.rate_capability(
    "SKL-TERN-001",
    quality=0.92,
    coverage=0.85,
    usability=0.88,
)
print(f"Overall: {rating['overall']:.2f}")
```

### 版本管理

```python
# 查询当前版本
version_info = sharing.version_control("SKL-TERN-001")

# 发布新版本
result = sharing.version_control(
    "SKL-TERN-001",
    new_version="1.1.0",
    changelog="增加自动扩容决策支持",
    breaking=False,
)
```

### 搜索与列表

```python
# 搜索能力
results = sharing.search_capabilities("数据库", tags=["performance"])

# 列出所有能力
all_caps = sharing.list_capabilities(status="published")
```

---

## 十四、Agent 协作进化（Collaborative Evolution）

> 参考 Accio Work：Agent 组团队、分工协作
> 模块：`evolution/collaborative.py` → `CollaborativeEvolution`

### 目录结构

```
evolution/teams/                  # Agent 团队配置
evolution/teams/{team_id}/
    team.json                      # 团队配置
    members.json                   # 成员信息
    shared_state.json              # 共享进化状态
    votes.jsonl                    # 决策投票记录
```

### 组建 Agent 团队

```python
from evolution import CollaborativeEvolution

collab = CollaborativeEvolution()
team_id = collab.form_agent_team(
    name="业务优化团队",
    strategy="hierarchical",   # hierarchical | democratic | specialized
    members=[
        {
            "agent_id": "A1", "name": "Explorer-A",
            "role": "explorer",           # 探索新模式
            "specialization": ["data_analysis", "pattern_mining"],
            "authority_level": 1,          # 1=观察, 2=建议, 3=决策
        },
        {
            "agent_id": "A2", "name": "Verifier-B",
            "role": "verifier",            # 验证假设可行性
            "specialization": ["validation", "testing"],
            "authority_level": 2,
        },
        {
            "agent_id": "A3", "name": "Synthesizer-C",
            "role": "synthesizer",         # 综合决策
            "specialization": ["decision_making"],
            "authority_level": 3,
        },
        {
            "agent_id": "A4", "name": "Critic-D",
            "role": "critic",              # 识别风险
            "specialization": ["risk_analysis"],
            "authority_level": 2,
        },
    ]
)
print(f"Team: {team_id}")
```

### 分布式学习

```python
# 各 Agent 独立探索后汇总发现
results = collab.distribute_learning(
    team_id,
    task="优化数据库查询策略",
    exploration_epochs=3,
)
# 返回：各 Agent 探索结果 + 综合建议
```

### 同步进化状态

```python
# 同步基因快照、模式和规则到团队共享状态
sync = collab.sync_evolution_state(team_id)
print(f"Synced: {sync['genes_count']} genes, "
      f"{sync['patterns_count']} patterns, "
      f"{sync['rules_count']} rules")
```

### 集体智慧决策

```python
decision = collab.collective_intelligence(
    team_id,
    topic="是否采用新的缓存策略",
    options=["adopt", "reject", "defer"],
    context={"current_hit_rate": 0.65, "target": 0.85, "complexity": "medium"},
    threshold=0.6,
)
print(f"Decision: {decision.result}")       # adopted / rejected / deferred
print(f"Reasoning:\n{decision.reasoning}")
print(f"Votes: {dict(decision.votes)}")      # {A1: adopt, A2: defer, ...}
```

### 团队管理

```python
# 查看团队状态
status = collab.get_team_status(team_id)
print(f"Decisions adopted: {status['performance_metrics']['decisions_adopted']}")

# 列出所有团队
teams = collab.list_teams(status="active")

# 解散团队
collab.dissolve_team(team_id)
```

---

## 十五、持续学习闭环

```
执行任务 → 记录结果 → 评估效果 → 提取模式 → 验证晋升 → 应用到下次
    ↑                                                        ↓
    ←←←←←←←←←← 反馈迭代 ←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←
```

三层进化环：

| 层次 | 组件 | 频率 | 驱动 |
|------|------|------|------|
| **微进化** | PerformanceTrigger / FailureTrigger | 实时 | 指标异常 |
| **中进化** | BusinessDrivenEvolution | 每轮任务后 | 决策质量评分 |
| **宏进化** | CollaborativeEvolution | 按需 | 重大业务决策 |

四类触发器（原有）→ 业务驱动进化（新）→ 能力共享（新）→ Agent 协作（新）

---

*M-A3 Self-Evolution System v1.1.0 | 构建于 2026-04-13 | 增强版：Accio 自我进化机制集成*
