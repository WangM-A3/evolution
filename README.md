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

*M-A3 Self-Evolution System v1.0.0 | 构建于 2026-04-13*
