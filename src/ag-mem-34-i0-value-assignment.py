#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-34
模块名称: 基础重要度I₀赋值单元
所属分区: 三、漏斗二：任务经验漏斗 / 三维重要度计算引擎
核心职责: 在每条新经验写入漏斗二时，基于经验的生成来源、任务类型、场景标签与语义特征，
          赋予基础重要度初始值I₀（0.0–1.0）。I₀是三维重要度I值的计算起点，反映该经验
          在未被任何S/V/C信号增强或衰减前的“先天重要性”。不同来源和类型的经验拥有不同
          的I₀基线：ECC主动请求记录的经验高于被动观测经验，工具调用类高于对话交互类，
          高风险操作高于普通操作。不参与后续重要度聚合计算或晋升决策，仅负责I₀的初始赋值。

依赖模块:
    ag-mem-15~19(各场景分槽), ag-mem-31(安全显著性S值计算单元),
    ag-mem-35(三维权重系数配置单元)
被依赖模块:
    ag-mem-36(综合重要度I值聚合计算单元), ag-mem-35

安全约束:
  I-01: I₀赋值规则（基线表、系数表、加分项表）为只读配置，运行时不得被任何模块修改
  I-02: I₀值下限为0.05，确保每条经验至少有极低的基础重要度，不会被立即遗忘
  I-03: I₀值上限为1.0，防止初始赋值溢出导致后续聚合计算失真
  I-04: I₀赋值结果必须可追溯，每个I₀值均附带赋值依据摘要与调整因子列表
"""

from typing import Dict, List, Optional, Any, Callable, Tuple
from dataclasses import dataclass, field
from enum import Enum
import time
import uuid


class AssignmentState(Enum):
    IDLE = "idle"
    ASSIGNING = "assigning"
    SYSTEM_PAUSED = "system_paused"


@dataclass
class I0AssignmentRequest:
    entry_id: str = ""
    generation_source: str = ""               # 经验生成来源
    task_type: str = ""                       # 任务类型
    scene_label: str = ""                     # 场景标签
    is_sensitive_operation: bool = False      # 是否涉及敏感操作
    is_privacy_access: bool = False           # 是否涉及隐私数据
    execution_duration_ratio: float = 1.0     # 执行耗时 / 历史平均耗时
    is_user_skipped: bool = False             # 用户是否主动跳过
    is_new_scene: bool = False                # 是否该分槽中首次出现
    s_value_reference: float = 0.0            # S值参考（可选）
    timestamp: float = field(default_factory=time.time)


@dataclass
class I0AssignmentResult:
    entry_id: str = ""
    i0_value: float = 0.0
    basis_summary: str = ""
    adjustment_factors: List[str] = field(default_factory=list)


@dataclass
class I0AssignmentStatus:
    state: str = ""
    recent_distribution: Dict[str, int] = field(default_factory=dict)
    avg_by_source: Dict[str, float] = field(default_factory=dict)


class I0AssignmentUnit:
    # 经验生成来源基线（只读）
    SOURCE_BASELINE = {
        "ECC主动请求记录": 0.60,
        "任务执行自动记录-成功": 0.45,
        "任务执行自动记录-失败": 0.50,
        "用户显式反馈触发记录": 0.55,
        "被动观测记录": 0.30,
        "系统安全事件触发记录": 0.70,
    }

    # 任务类型调整系数（只读）
    TASK_TYPE_COEFFICIENT = {
        "工具调用": 1.0,
        "信息检索": 0.9,
        "对话交互": 0.8,
        "创作生成": 0.95,
        "通用任务": 0.85,
    }

    # 额外加分项
    SENSITIVE_OP_BONUS = 0.20
    PRIVACY_ACCESS_BONUS = 0.15
    ABNORMAL_DURATION_BONUS = 0.10
    USER_SKIP_PENALTY = -0.10
    NEW_SCENE_BONUS = 0.05

    # 默认基线
    DEFAULT_BASELINE = 0.40
    DEFAULT_COEFFICIENT = 0.85

    # 上下限
    MIN_I0 = 0.05
    MAX_I0 = 1.0

    STATUS_REPORT_INTERVAL_SEC = 60

    def __init__(self):
        self.module_id = "ag-mem-34"
        self.module_name = "基础重要度I₀赋值单元"
        self.version = "V1.0"

        self.state = AssignmentState.IDLE
        self._distribution: Dict[str, int] = {}
        self._source_sums: Dict[str, float] = {}
        self._source_counts: Dict[str, int] = {}
        self._last_status_time: float = 0.0
        self._pending_logs: List[Dict[str, Any]] = []

        # 回调注入
        self._query_assignment_request = None
        self._query_weight_config = None

        self._publish_assignment_result = None
        self._publish_status_report = None
        self._publish_event_log = None

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    # ========== 回调注入 ==========
    def set_assignment_request_query(self, callback: Callable[[], Optional[I0AssignmentRequest]]):
        self._query_assignment_request = callback

    def set_weight_config_query(self, callback: Callable[[], Optional[Dict[str, Any]]]):
        self._query_weight_config = callback

    def set_assignment_result_publisher(self, callback: Callable[[I0AssignmentResult], None]):
        self._publish_assignment_result = callback

    def set_status_report_publisher(self, callback: Callable[[I0AssignmentStatus], None]):
        self._publish_status_report = callback

    def set_event_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_event_log = callback

    # ========== 主循环 ==========
    def run_assignment_cycle(self) -> Optional[I0AssignmentResult]:
        now = time.time()

        if self.state == AssignmentState.SYSTEM_PAUSED:
            return None

        # 定期状态上报
        if now - self._last_status_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._publish_status()
            self._last_status_time = now

        request = self._query_assignment_request() if self._query_assignment_request else None
        if request is None:
            return None

        self.state = AssignmentState.ASSIGNING
        result = self._assign_i0(request)
        self.state = AssignmentState.IDLE

        if self._publish_assignment_result:
            self._publish_assignment_result(result)

        return result

    # ========== 核心赋值 ==========
    def _assign_i0(self, request: I0AssignmentRequest) -> I0AssignmentResult:
        # 1. 获取来源基线
        baseline = self.SOURCE_BASELINE.get(request.generation_source, self.DEFAULT_BASELINE)
        basis_parts = [f"来源基线={baseline}"]

        # 2. 获取任务类型调整系数
        coefficient = self.TASK_TYPE_COEFFICIENT.get(request.task_type, self.DEFAULT_COEFFICIENT)
        basis_parts.append(f"任务系数={coefficient}")

        # 3. 计算基础I₀
        i0_value = baseline * coefficient
        adjustment_factors = []

        # 4. 应用额外加分/减分项
        if request.is_sensitive_operation:
            i0_value += self.SENSITIVE_OP_BONUS
            adjustment_factors.append(f"敏感操作+{self.SENSITIVE_OP_BONUS}")

        if request.is_privacy_access:
            i0_value += self.PRIVACY_ACCESS_BONUS
            adjustment_factors.append(f"隐私访问+{self.PRIVACY_ACCESS_BONUS}")

        if request.execution_duration_ratio > 2.0:
            i0_value += self.ABNORMAL_DURATION_BONUS
            adjustment_factors.append(f"异常耗时+{self.ABNORMAL_DURATION_BONUS}")

        if request.is_user_skipped:
            i0_value += self.USER_SKIP_PENALTY
            adjustment_factors.append(f"用户跳过{self.USER_SKIP_PENALTY}")

        if request.is_new_scene:
            i0_value += self.NEW_SCENE_BONUS
            adjustment_factors.append(f"新场景+{self.NEW_SCENE_BONUS}")

        # 5. 边界裁剪
        i0_value = max(self.MIN_I0, min(self.MAX_I0, round(i0_value, 2)))

        # 6. 更新统计
        # 分布统计（按区间）
        if i0_value >= 0.8:
            bucket = "0.8-1.0"
        elif i0_value >= 0.6:
            bucket = "0.6-0.8"
        elif i0_value >= 0.4:
            bucket = "0.4-0.6"
        elif i0_value >= 0.2:
            bucket = "0.2-0.4"
        else:
            bucket = "0.05-0.2"
        self._distribution[bucket] = self._distribution.get(bucket, 0) + 1

        # 按来源统计
        source = request.generation_source if request.generation_source else "未知"
        self._source_sums[source] = self._source_sums.get(source, 0.0) + i0_value
        self._source_counts[source] = self._source_counts.get(source, 0) + 1

        return I0AssignmentResult(
            entry_id=request.entry_id,
            i0_value=i0_value,
            basis_summary=", ".join(basis_parts),
            adjustment_factors=adjustment_factors
        )

    # ========== 辅助方法 ==========
    def _publish_status(self):
        avg_by_source = {}
        for source in self._source_sums:
            count = self._source_counts.get(source, 1)
            avg_by_source[source] = round(self._source_sums[source] / count, 3)

        if self._publish_status_report:
            self._publish_status_report(I0AssignmentStatus(
                state=self.state.value,
                recent_distribution=self._distribution,
                avg_by_source=avg_by_source
            ))

    def get_state(self) -> AssignmentState:
        return self.state

    def emergency_shutdown(self):
        self.state = AssignmentState.SYSTEM_PAUSED
        print(f"[{self.module_id}] 紧急熔断")

    def _log_event(self, event_type: str, details: Dict[str, Any]):
        entry = {
            "log_id": f"log-{uuid.uuid4().hex[:8]}",
            "event_type": event_type,
            "source_module": self.module_id,
            "details": details,
            "timestamp": time.time()
        }
        self._pending_logs.append(entry)
        if self._publish_event_log:
            self._publish_event_log(entry)

    def collect_pending_logs(self) -> List[Dict[str, Any]]:
        logs = self._pending_logs.copy()
        self._pending_logs.clear()
        return logs


# ========== 演示与测试 ==========
def print_separator(title: str):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def demo_main():
    print("=" * 70)
    print("  Agent-mlnf-mem 基础重要度I₀赋值单元 (ag-mem-34) 演示")
    print("=" * 70)

    assigner = I0AssignmentUnit()

    print_separator("STEP 1: ECC主动请求+工具调用 → I₀较高")
    assigner.set_assignment_request_query(lambda: I0AssignmentRequest(
        entry_id="E01",
        generation_source="ECC主动请求记录",
        task_type="工具调用"
    ))
    result = assigner.run_assignment_cycle()
    if result:
        print(f"  I₀={result.i0_value}, 依据={result.basis_summary}, 调整={result.adjustment_factors}")

    print_separator("STEP 2: 被动观测+对话交互 → I₀较低")
    assigner.set_assignment_request_query(lambda: I0AssignmentRequest(
        entry_id="E02",
        generation_source="被动观测记录",
        task_type="对话交互"
    ))
    result = assigner.run_assignment_cycle()
    if result:
        print(f"  I₀={result.i0_value}")

    print_separator("STEP 3: 安全事件+敏感操作 → I₀极高")
    assigner.set_assignment_request_query(lambda: I0AssignmentRequest(
        entry_id="E03",
        generation_source="系统安全事件触发记录",
        task_type="工具调用",
        is_sensitive_operation=True
    ))
    result = assigner.run_assignment_cycle()
    if result:
        print(f"  I₀={result.i0_value}, 调整={result.adjustment_factors}")

    print("\n✅ 基础重要度I₀赋值单元演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("ag-mem-34 基础重要度I₀赋值单元 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        def setup_assigner():
            return I0AssignmentUnit()

        # TC-M34-01: ECC主动请求+工具调用
        print("\n[TC-M34-01] ECC主动请求+工具调用")
        try:
            a = setup_assigner()
            a.set_assignment_request_query(lambda: I0AssignmentRequest(
                entry_id="T01", generation_source="ECC主动请求记录", task_type="工具调用"
            ))
            result = a.run_assignment_cycle()
            assert result.i0_value == 0.60  # 0.60 * 1.0
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M34-02: 被动观测+对话交互
        print("\n[TC-M34-02] 被动观测+对话交互")
        try:
            a = setup_assigner()
            a.set_assignment_request_query(lambda: I0AssignmentRequest(
                entry_id="T02", generation_source="被动观测记录", task_type="对话交互"
            ))
            result = a.run_assignment_cycle()
            assert result.i0_value == 0.24  # 0.30 * 0.8
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M34-03: 安全事件+敏感操作
        print("\n[TC-M34-03] 安全事件+敏感操作")
        try:
            a = setup_assigner()
            a.set_assignment_request_query(lambda: I0AssignmentRequest(
                entry_id="T03", generation_source="系统安全事件触发记录", task_type="工具调用",
                is_sensitive_operation=True
            ))
            result = a.run_assignment_cycle()
            assert result.i0_value == 0.90  # 0.70 + 0.20
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M34-04: 任务失败+用户跳过
        print("\n[TC-M34-04] 任务失败+用户跳过")
        try:
            a = setup_assigner()
            a.set_assignment_request_query(lambda: I0AssignmentRequest(
                entry_id="T04", generation_source="任务执行自动记录-失败", task_type="通用任务",
                is_user_skipped=True
            ))
            result = a.run_assignment_cycle()
            assert result.i0_value == 0.33  # 0.50*0.85=0.425, 减去0.10=0.325, 四舍五入0.33
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M34-05: 未知来源使用默认值
        print("\n[TC-M34-05] 未知来源使用默认值")
        try:
            a = setup_assigner()
            a.set_assignment_request_query(lambda: I0AssignmentRequest(
                entry_id="T05", generation_source="未知来源", task_type="未知类型"
            ))
            result = a.run_assignment_cycle()
            assert result.i0_value == 0.34  # 0.40 * 0.85
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M34-06: 紧急熔断
        print("\n[TC-M34-06] 紧急熔断")
        try:
            a = setup_assigner()
            a.emergency_shutdown()
            assert a.state == AssignmentState.SYSTEM_PAUSED
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        print("\n" + "=" * 60)
        print(f"测试结果: {passed} PASS, {failed} FAIL")
        print("=" * 60)
    else:
        demo_main()