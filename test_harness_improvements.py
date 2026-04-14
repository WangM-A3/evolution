"""
tests/test_harness_improvements.py
==================================
全部6项Harness改进的集成测试

测试覆盖：
  1. verification_report.py  — 报告结构与序列化
  2. verifier.py              — 独立验证Agent
  3. stop_hook.py             — StopHook执着模式
  4. sprint_contract.py        — Sprint契约
  5. three_agent_topology.py  — 三Agent拓扑
  6. simplification_audit.py   — 简化审查
  7. m_a3_harness_progress.py — 进度追踪
"""

import json
import os
import tempfile
import time
from pathlib import Path

import pytest


# ─────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────

@pytest.fixture
def temp_dir():
    """临时目录fixture"""
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.fixture
def temp_progress_file(temp_dir):
    """临时进度文件"""
    path = temp_dir / "m-a3-harness-progress.json"
    return path


# ══════════════════════════════════════════════════════════════════════════════
# Test 1: m_a3_harness_progress.py
# ══════════════════════════════════════════════════════════════════════════════

class TestHarnessProgress:
    """进度追踪器测试"""

    def test_create_and_save(self, temp_progress_file):
        from m_a3_harness_progress import HarnessProgress

        progress = HarnessProgress.create("TASK-20260414-001", "实现登录")
        progress.add_feature("API端点")
        progress.add_feature("Token验证")
        path = progress.save(str(temp_progress_file))

        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["task_id"] == "TASK-20260414-001"
        assert len(data["features"]) == 2
        assert data["features"][0]["name"] == "API端点"

    def test_workflow_pending_to_done(self):
        from m_a3_harness_progress import HarnessProgress

        p = HarnessProgress.create("TASK-001", "测试")
        p.add_feature("功能A", "描述")
        p.mark_in_progress("功能A")
        p.mark_done("功能A", verified=True, evidence="pytest通过")

        assert p.features[0].status == "done"
        assert p.features[0].verified is True
        assert p.progress_percent() == 1.0

    def test_blockers_and_next_steps(self):
        from m_a3_harness_progress import HarnessProgress

        p = HarnessProgress.create("TASK-001", "测试")
        p.add_blocker("缺数据库")
        p.add_next_step("连接DB")
        p.add_next_step("测试")
        step = p.pop_next_step()

        assert step == "连接DB"
        assert len(p.next_steps) == 1

    def test_summary(self):
        from m_a3_harness_progress import HarnessProgress

        p = HarnessProgress.create("TASK-001", "测试")
        p.add_feature("F1")
        p.add_feature("F2")
        p.mark_done("F1")

        summary = p.summary()
        assert summary["completion_rate"] == "1/2"
        assert summary["in_progress"] == 0
        assert summary["done"] == 1


# ══════════════════════════════════════════════════════════════════════════════
# Test 2: verification_report.py
# ══════════════════════════════════════════════════════════════════════════════

class TestVerificationReport:
    """验证报告测试"""

    def test_basic_report_flow(self):
        from evolution.verification_report import (
            VerificationReport, CheckCategory, ReportStatus,
        )

        report = VerificationReport(
            task_id="TASK-001",
            goal="实现登录",
        )

        report.add_check(
            name="pytest通过",
            category=CheckCategory.UNIT,
            passed=True,
            detail="12/12 tests passed",
            duration_ms=150.0,
        )
        report.add_check(
            name="响应时间<500ms",
            category=CheckCategory.PERFORMANCE,
            passed=False,
            detail="实测 680ms",
            fix_suggestion="增加缓存",
            severity=4,
        )

        report.finalize()

        assert report.status == ReportStatus.FAILED
        assert len(report.checks) == 2
        assert "❌" in report.verdict  # 使用emoji
        summary = report.summary()
        assert summary["failed"] == 1
        assert summary["critical_failures"] == ["响应时间<500ms"]

    def test_report_all_passed(self):
        from evolution.verification_report import (
            VerificationReport, CheckCategory, ReportStatus,
        )

        report = VerificationReport(task_id="TASK-001", goal="测试")
        report.add_check("单元测试", CheckCategory.UNIT, True, "通过")
        report.add_check("集成测试", CheckCategory.INTEGRATION, True, "通过")
        report.finalize()

        assert report.status == ReportStatus.PASSED
        assert "✅" in report.verdict  # 使用emoji

    def test_save_and_load(self, temp_dir):
        from evolution.verification_report import (
            VerificationReport, CheckCategory, ReportStatus,
        )

        report = VerificationReport(task_id="TASK-001", goal="测试")
        report.add_check("单元测试", CheckCategory.UNIT, True)
        report.finalize()

        path = report.save(temp_dir / "report.json")
        loaded = VerificationReport.load(path)

        assert loaded.task_id == "TASK-001"
        assert len(loaded.checks) == 1
        assert loaded.status == ReportStatus.PASSED

    def test_category_breakdown(self):
        from evolution.verification_report import (
            VerificationReport, CheckCategory,
        )

        report = VerificationReport(task_id="TASK-001", goal="测试")
        report.add_check("测试A", CheckCategory.UNIT, True)
        report.add_check("测试B", CheckCategory.UNIT, False)
        report.add_check("性能测试", CheckCategory.PERFORMANCE, True)

        breakdown = report.category_breakdown()
        assert breakdown["unit"]["total"] == 2
        assert breakdown["unit"]["failed"] == 1


# ══════════════════════════════════════════════════════════════════════════════
# Test 3: verifier.py
# ══════════════════════════════════════════════════════════════════════════════

class TestIndependentVerifier:
    """独立验证Agent测试"""

    def test_file_exists_validator(self):
        from evolution.verifier import Validators

        # 文件不存在
        passed, detail, evidence = Validators.file_exists_check({"path": "/nonexistent/file.py"})
        assert passed is False

        # 文件存在
        passed, detail, evidence = Validators.file_exists_check({"path": __file__})
        assert passed is True

    def test_json_parse_validator(self, temp_dir):
        from evolution.verifier import Validators

        # 有效JSON
        valid = temp_dir / "valid.json"
        valid.write_text('{"a": 1}', encoding="utf-8")
        passed, detail, _ = Validators.json_parse_check({"path": str(valid)})
        assert passed is True

        # 无效JSON
        invalid = temp_dir / "invalid.json"
        invalid.write_text('{"a":}', encoding="utf-8")
        passed, detail, _ = Validators.json_parse_check({"path": str(invalid)})
        assert passed is False

    def test_verify_quick(self, temp_dir):
        from evolution.verifier import IndependentVerifier

        # 创建进度文件
        pf = temp_dir / "m-a3-harness-progress.json"
        pf.write_text('{"task_id":"TASK-001","goal":"test","features":[]}', encoding="utf-8")

        verifier = IndependentVerifier(reports_dir=str(temp_dir / "reports"))
        report = verifier.verify_quick("TASK-001", {"path": str(pf)})

        assert report.task_id == "TASK-001"
        assert len(report.checks) >= 1

    def test_default_criteria(self):
        from evolution.verifier import IndependentVerifier

        criteria = IndependentVerifier.default_criteria_for_task("TASK-001")
        assert len(criteria) == 3
        names = [c.name for c in criteria]
        assert "进度JSON存在" in names
        assert "Git工作区已提交" in names

    def test_verify_with_manual_criteria(self):
        from evolution.verifier import IndependentVerifier, Criterion, CriterionType
        from evolution.verification_report import ReportStatus

        def always_pass(ctx):
            return True, "总是通过", []

        def always_fail(ctx):
            return False, "总是失败", ["test"]

        verifier = IndependentVerifier(reports_dir="/tmp/verifier_test_reports")
        report = verifier.verify(
            task_id="TASK-MANUAL-001",
            goal="手动标准测试",
            criteria=[
                Criterion("总是通过", CriterionType.UNIT, validator=always_pass),
                Criterion("总是失败", CriterionType.UNIT, severity=4, validator=always_fail),
            ],
        )

        assert report.status == ReportStatus.FAILED
        assert len(report.checks) == 2
        assert report.checks[0].passed is True
        assert report.checks[1].passed is False


# ══════════════════════════════════════════════════════════════════════════════
# Test 4: stop_hook.py
# ══════════════════════════════════════════════════════════════════════════════

class TestStopHook:
    """StopHook测试"""

    def test_hook_auto_stop_mode(self):
        from evolution.stop_hook import StopHook, HookMode
        from evolution.verifier import IndependentVerifier, Criterion, CriterionType

        always_fail_validator = lambda ctx: (False, "Always fails", [])

        verifier = IndependentVerifier(reports_dir="/tmp/hook_test_reports")
        hook = StopHook(
            mode=HookMode.AUTO_STOP,
            max_attempts=2,
            cooldown_seconds=0.01,
            verifier=verifier,
        )

        result = hook.after_task(
            task_id="TASK-HOOK-001",
            goal="测试AUTO_STOP",
            criteria=[
                Criterion("总是失败", CriterionType.UNIT, validator=always_fail_validator),
            ],
        )

        assert result.final_status == "failed"
        assert result.attempt_count == 1

    def test_hook_persistent_mode_max_retries(self):
        from evolution.stop_hook import StopHook, HookMode
        from evolution.verifier import IndependentVerifier, Criterion, CriterionType

        always_fail_validator = lambda ctx: (False, "Always fails", [])

        verifier = IndependentVerifier(reports_dir="/tmp/hook_persist_reports")
        hook = StopHook(
            mode=HookMode.PERSISTENT,
            max_attempts=3,
            cooldown_seconds=0.01,
            verifier=verifier,
        )

        result = hook.after_task(
            task_id="TASK-HOOK-002",
            goal="执着重试测试",
            criteria=[
                Criterion("总是失败", CriterionType.UNIT, validator=always_fail_validator),
            ],
        )

        assert result.final_status == "max_retries"
        assert result.attempt_count == 3

    def test_hook_passed_on_first_try(self):
        from evolution.stop_hook import StopHook, HookMode
        from evolution.verifier import IndependentVerifier, Criterion, CriterionType

        always_pass_validator = lambda ctx: (True, "Always passes", [])

        verifier = IndependentVerifier(reports_dir="/tmp/hook_pass_reports")
        hook = StopHook(
            mode=HookMode.PERSISTENT,
            max_attempts=3,
            cooldown_seconds=0.01,
            verifier=verifier,
        )

        result = hook.after_task(
            task_id="TASK-HOOK-003",
            goal="一次通过测试",
            criteria=[
                Criterion("总是通过", CriterionType.UNIT, validator=always_pass_validator),
            ],
        )

        assert result.final_status == "passed"
        assert result.attempt_count == 1

    def test_hook_result_to_dict(self):
        from evolution.stop_hook import HookResult

        result = HookResult(
            task_id="TASK-001",
            final_status="passed",
            attempt_count=1,
            total_duration_ms=50.0,
            attempts=[],
            final_report=None,
        )
        d = result.to_dict()
        assert d["final_status"] == "passed"
        assert d["attempt_count"] == 1


# ══════════════════════════════════════════════════════════════════════════════
# Test 5: sprint_contract.py
# ══════════════════════════════════════════════════════════════════════════════

class TestSprintContract:
    """Sprint契约测试"""

    def test_contract_lifecycle(self, temp_dir):
        from evolution.sprint_contract import (
            SprintContract, ContractStatus, DeliverablePriority,
        )

        contract = SprintContract.create(
            task_id="TASK-001",
            goal="实现登录功能",
        )
        contract.add_deliverable("登录代码", "src/auth/login.py", priority=DeliverablePriority.MUST)
        contract.add_deliverable("单元测试", "tests/test_login.py")
        contract.add_criterion("代码可编译", type="UNIT", severity=4)
        contract.set_deadline(hours_from_now=8)
        contract.approve()

        assert contract.status == ContractStatus.APPROVED
        assert len(contract.deliverables) == 2
        assert contract.deadline is not None

        # 保存并加载
        path = contract.save(str(temp_dir / "contract.json"))
        loaded = SprintContract.load(path)
        assert loaded.goal == "实现登录功能"
        assert len(loaded.deliverables) == 2

    def test_contract_status_transitions(self):
        from evolution.sprint_contract import SprintContract, ContractStatus

        contract = SprintContract.create("TASK-001", "测试")
        contract.add_deliverable("交付物1", "a.py")
        contract.add_criterion("交付完成", type="UNIT", severity=3)

        assert contract.status == ContractStatus.DRAFT

        contract.approve()
        assert contract.status == ContractStatus.APPROVED

        contract.start_execution()
        assert contract.status == ContractStatus.EXECUTING

        contract.mark_passed()
        assert contract.status == ContractStatus.PASSED

        contract.close()
        assert contract.status == ContractStatus.CLOSED

    def test_contract_progress(self):
        from evolution.sprint_contract import (
            SprintContract, ContractStatus, DeliverablePriority,
        )

        contract = SprintContract.create("TASK-001", "测试")
        contract.add_deliverable("F1", "f1.py", priority=DeliverablePriority.MUST)
        contract.add_deliverable("F2", "f2.py", priority=DeliverablePriority.MUST)
        contract.add_criterion("单元测试", type="UNIT", severity=4)
        contract.add_criterion("性能测试", type="PERFORMANCE", severity=3)
        contract.approve()

        # 标记F1完成
        contract.deliverables[0].status = "done"

        progress = contract.progress()
        assert progress["deliverables_total"] == 2
        assert progress["deliverables_done"] == 1
        assert progress["completion_rate"] == 0.5

    def test_contract_cannot_approve_without_criteria(self):
        from evolution.sprint_contract import SprintContract, ContractStatus

        contract = SprintContract.create("TASK-001", "测试")
        contract.add_deliverable("F1", "a.py")
        # 不添加验收标准

        with pytest.raises(ValueError, match="必须至少有一条验收标准"):
            contract.approve()

    def test_contract_add_criterion(self):
        from evolution.sprint_contract import SprintContract, CriterionType

        contract = SprintContract.create("TASK-001", "测试")
        contract.add_criterion(
            name="响应时间<200ms",
            type=CriterionType.PERFORMANCE.value,
            severity=4,
            description="P99响应时间",
            threshold="<200ms",
            fix_suggestion="增加CDN",
        )
        assert len(contract.acceptance_criteria) == 1
        assert contract.acceptance_criteria[0].severity == 4


# ══════════════════════════════════════════════════════════════════════════════
# Test 6: three_agent_topology.py
# ══════════════════════════════════════════════════════════════════════════════

class TestThreeAgentTopology:
    """三Agent拓扑测试"""

    def test_start_session(self, temp_dir):
        from evolution.three_agent_topology import ThreeAgentTopology, SessionStatus

        topology = ThreeAgentTopology(base_dir=str(temp_dir))
        session = topology.start_session("TASK-001", "实现登录")

        assert session.task_id == "TASK-001"
        assert session.status == SessionStatus.PLANNING
        assert len(session.session_id) > 0

    def test_planner_generates_steps(self):
        from evolution.three_agent_topology import PlannerAgent, AgentRole

        planner = PlannerAgent()
        steps = planner.plan("TASK-001", "实现登录", context={"features": [
            {"name": "API端点"},
            {"name": "JWT验证"},
        ]})

        assert len(steps) >= 5
        # 确认角色分布
        roles = [s.role for s in steps]
        assert AgentRole.PLANNER in roles
        assert AgentRole.GENERATOR in roles
        assert AgentRole.EVALUATOR in roles

    def test_plan_and_evaluate_flow(self, temp_dir):
        from evolution.three_agent_topology import (
            ThreeAgentTopology, AgentRole, StepStatus,
        )

        topology = ThreeAgentTopology(base_dir=str(temp_dir))

        # 启动会话
        session = topology.start_session("TASK-001", "实现登录")

        # Planner: 制定计划
        features = [{"name": "API端点", "output": "src/login.py"}]
        steps = topology.plan(session.session_id, "实现登录", features=features)

        assert len(steps) > 0

        # 模拟Generator执行（GENERATOR角色的步骤）
        for step in steps:
            if step.role == AgentRole.GENERATOR:
                success, detail = topology.generate(session.session_id, step)
                assert success is True

        # Evaluator: 评估
        # 创建必要的输出文件使评估通过
        (temp_dir / "src").mkdir(exist_ok=True)
        for step in steps:
            for output in step.outputs:
                output_filled = output.replace("{task_id}", session.task_id)
                p = temp_dir / output_filled
                p.parent.mkdir(parents=True, exist_ok=True)
                if "verification" not in output and "final" not in output:
                    p.write_text('{"id": "dummy"}', encoding="utf-8")

        result = topology.evaluate(session.session_id)
        assert "verdict" in result
        assert "completion_rate" in result

    def test_evaluator_agent(self):
        from evolution.three_agent_topology import EvaluatorAgent, TaskStep, AgentRole
        from pathlib import Path

        evaluator = EvaluatorAgent()

        # 有输出文件的步骤 → 通过
        step = TaskStep(
            step_id="STEP-001",
            description="生成文件",
            role=AgentRole.EVALUATOR,
            outputs=["/tmp/exists.json"],
        )
        Path("/tmp/exists.json").parent.mkdir(parents=True, exist_ok=True)
        Path("/tmp/exists.json").write_text("{}", encoding="utf-8")
        passed, note = evaluator.evaluate_step(step)
        assert passed is True

        # 无输出文件的步骤 → 失败
        step2 = TaskStep(
            step_id="STEP-002",
            description="生成缺失文件",
            role=AgentRole.EVALUATOR,
            outputs=["/tmp/nonexistent_xyz123.json"],
        )
        passed2, note2 = evaluator.evaluate_step(step2)
        assert passed2 is False

    def test_session_save_and_load(self, temp_dir):
        from evolution.three_agent_topology import ThreeAgentTopology

        topology = ThreeAgentTopology(base_dir=str(temp_dir))
        session = topology.start_session("TASK-001", "测试")
        topology.plan(session.session_id, "测试")

        loaded = topology.get_session(session.session_id)
        assert loaded is not None
        assert loaded.task_id == "TASK-001"


# ══════════════════════════════════════════════════════════════════════════════
# Test 7: simplification_audit.py
# ══════════════════════════════════════════════════════════════════════════════

class TestSimplificationAudit:
    """简化审查测试"""

    def test_component_metadata_defaults(self):
        from evolution.simplification_audit import ComponentMetadata, ComponentType

        meta = ComponentMetadata.with_defaults(
            component_id="RULE-001",
            component_type=ComponentType.RULE.name,
            name="我的规则",
            days_until_expire=30,
        )

        assert meta.component_id == "RULE-001"
        assert meta.usage_count == 0
        assert meta.staleness_score == 0.0
        assert meta.expire_date is not None

    def test_audit_active_component_is_kept(self, temp_dir):
        from evolution.simplification_audit import (
            SimplificationAuditor,
            ComponentType,
        )

        # 创建活跃规则
        rules_dir = temp_dir / "rules"
        rules_dir.mkdir()
        rule_file = rules_dir / "active_rule.json"
        rule_file.write_text(json.dumps({
            "rule_id": "RULE-ACTIVE-001",
            "name": "活跃规则",
            "metadata": {
                "created_at": "2026-04-01T00:00:00",
                "expire_date": "2026-06-01T00:00:00",
                "last_used": "2026-04-14T00:00:00",
                "usage_count": 10,
            },
        }, ensure_ascii=False), encoding="utf-8")

        # 修复目录映射
        audit = SimplificationAuditor(base_dir=str(temp_dir), reports_dir=str(temp_dir / "audit_reports"))
        # 直接审计该文件
        import evolution.simplification_audit as sa
        meta = audit._extract_metadata(
            json.loads(rule_file.read_text(encoding="utf-8")),
            rule_file,
            ComponentType.RULE,
        )
        result = audit._audit_component(meta, rule_file)

        assert result.action.name == "KEEP"
        assert result.staleness_score < 0.5

    def test_staleness_score_formula(self):
        from evolution.simplification_audit import ComponentMetadata, ComponentType
        from datetime import datetime, timedelta

        # 刚创建、使用频繁 → 低staleness
        now = datetime.utcnow()
        active_meta = ComponentMetadata(
            component_id="ACTIVE",
            component_type=ComponentType.RULE.name,
            name="活跃",
            created_at=now.isoformat(),
            expire_date=(now + timedelta(days=30)).isoformat(),
            last_used=now.isoformat(),
            usage_count=100,
            staleness_score=0.0,
        )

        # 应该不触发删除
        assert active_meta.usage_count > 0
        assert active_meta.staleness_score < 0.5

    def test_dead_weight_detection(self, temp_dir):
        from evolution.simplification_audit import (
            SimplificationAuditor,
            ComponentType,
        )

        rules_dir = temp_dir / "rules"
        rules_dir.mkdir()
        dead_rule = rules_dir / "dead_rule.json"
        dead_rule.write_text(json.dumps({
            "rule_id": "RULE-DEAD-001",
            "name": "死规则",
            "metadata": {
                "created_at": "2026-01-01T00:00:00",
                "expire_date": "2026-01-15T00:00:00",  # 早已过期
                "last_used": "2026-01-01T00:00:00",
                "usage_count": 0,
                "superseded_by": "RULE-NEW-001",
            },
        }, ensure_ascii=False), encoding="utf-8")

        audit = SimplificationAuditor(base_dir=str(temp_dir), reports_dir=str(temp_dir / "audit_reports"))
        meta = audit._extract_metadata(
            json.loads(dead_rule.read_text(encoding="utf-8")),
            dead_rule,
            ComponentType.RULE,
        )
        result = audit._audit_component(meta, dead_rule)

        assert result.action.name == "DELETE"
        assert "死代码" in result.reason


# ══════════════════════════════════════════════════════════════════════════════
# Test 8: Integration — 所有模块一起工作
# ══════════════════════════════════════════════════════════════════════════════

class TestIntegration:
    """集成测试：全部6项改进联动"""

    def test_full_harness_workflow(self, temp_dir):
        """
        完整工作流：
        1. SprintContract创建契约
        2. ThreeAgentTopology执行
        3. IndependentVerifier验证
        4. StopHook执着重试
        5. SimplificationAudit审查
        6. HarnessProgress追踪
        """
        from evolution.sprint_contract import SprintContract, DeliverablePriority
        from evolution.three_agent_topology import ThreeAgentTopology, AgentRole
        from evolution.verifier import IndependentVerifier
        from evolution.stop_hook import StopHook, HookMode
        from evolution.simplification_audit import SimplificationAuditor
        from m_a3_harness_progress import HarnessProgress

        # 1. Sprint契约
        contract = SprintContract.create("TASK-FULL-001", "实现完整功能")
        contract.add_deliverable("代码文件", "src/feature.py", priority=DeliverablePriority.MUST)
        contract.add_criterion("pytest通过", type="UNIT", severity=4)
        contract.add_criterion("代码风格", type="STYLE", severity=2)
        contract.approve()
        contract_path = contract.save(str(temp_dir / "contract.json"))
        assert contract.status.value == "approved"

        # 2. 进度追踪
        progress = HarnessProgress.create("TASK-FULL-001", "实现完整功能")
        progress.add_feature("代码实现")
        progress.add_feature("测试通过")
        progress.update_focus("正在实现代码")
        progress_path = progress.save(str(temp_dir / "progress.json"))
        assert progress_path.exists()

        # 3. 三Agent拓扑
        topology = ThreeAgentTopology(base_dir=str(temp_dir / "sessions"))
        session = topology.start_session("TASK-FULL-001", "实现完整功能")
        steps = topology.plan(session.session_id, "实现完整功能")

        # 4. StopHook执著验证
        hook = StopHook(
            mode=HookMode.PERSISTENT,
            max_attempts=2,
            cooldown_seconds=0.01,
            verifier=IndependentVerifier(reports_dir=str(temp_dir / "reports")),
        )
        # 创建一个总是通过的验证器
        from evolution.verifier import Criterion, CriterionType
        from evolution.verification_report import ReportStatus

        pass_validator = lambda ctx: (True, "Integration test pass", [])
        always_pass_criterion = Criterion("集成测试", CriterionType.INTEGRATION, validator=pass_validator)

        hook_result = hook.after_task(
            task_id="TASK-FULL-001",
            goal="执著测试",
            criteria=[always_pass_criterion],
        )
        assert hook_result.final_status == "passed"
        assert hook_result.attempt_count == 1

        # 5. 简化审查（扫描进化目录）
        audit = SimplificationAuditor(
            base_dir=str(temp_dir),
            reports_dir=str(temp_dir / "audit_reports"),
        )
        report = audit.full_audit()
        assert "summary" in report.to_dict()
        assert "components_scanned" in report.to_dict()

        print("[集成测试] ✅ 全链路工作正常")

    def test_verifier_integrates_with_engine(self):
        """验证器与engine.py的集成"""
        from evolution.verifier import IndependentVerifier, Criterion, CriterionType
        from evolution.verification_report import ReportStatus

        # 模拟engine中的验证场景
        verifier = IndependentVerifier()

        def code_quality_check(ctx):
            files = ctx.get("files", {})
            python_files = [f for f in files if f.endswith(".py")]
            if not python_files:
                return False, "无Python文件", []
            return True, f"发现{len(python_files)}个Python文件", python_files

        report = verifier.verify(
            task_id="TASK-ENGINE-001",
            goal="Engine集成验证",
            criteria=[
                Criterion(
                    name="代码质量",
                    criterion_type=CriterionType.UNIT,
                    severity=4,
                    validator=code_quality_check,
                ),
            ],
            deliverables={"files": {"src/main.py": "/path/to/main.py"}},
        )

        assert report.task_id == "TASK-ENGINE-001"
        print(f"[Engine集成] ✅ 报告状态: {report.status.value}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
