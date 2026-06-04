#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-32
模块名称: 风格匹配度V值计算单元
所属分区: 三、漏斗二：任务经验漏斗 / 三维重要度计算引擎
核心职责: 从任务经验条目中提取与用户价值相关的行为信号与反馈数据，计算用户价值分值
          V（0.0–1.0）。V值反映该任务经验对当前用户的个性化价值：用户显式正向反馈、
          高频复用行为、长时间查看、复制分享等操作将获得较高V值；偶发行为或负向反馈
          获得较低V值。V值作为三维重要度I值的关键组成部分，直接影响经验条目的留存
          优先级与个性化推荐的准确性。不参与认知决策，仅执行V值的客观量化计算。

依赖模块:
    ag-mem-15~19(各场景分槽), ag-mem-10(偏好累积统计单元),
    ag-mem-35(三维权重系数配置单元)
被依赖模块:
    ag-mem-36(综合重要度I值聚合计算单元), ag-mem-35(三维权重系数配置单元)

安全约束:
  V-01: V值计算仅基于用户行为元数据（行为类型、频次、反馈标记），不得解析用户原始交互内容
  V-02: 用户偏好查询结果仅用于V值修正，不得将偏好数据写入经验条目本身
  V-03: 显式负向反馈的V值扣减必须有上限（最低0.05），不得扣减至负数
  V-04: V值计算结果必须可追溯，每个V值均附带触发的主要价值信号列表
  V-05: 分槽专属权重调整系数为只读配置，运行时不得修改

设计说明:
  快速判定路径（点赞+成功、点踩、首次偶发）返回的是硬规则标杆值，不受分槽调整系数影响。
  这是有意设计：快速判定的语义是"无论场景如何，该用户行为本身就决定了V值的上下限"。
  分槽调整仅作用于标准计算路径中通过多维度信号加权得出的V值，用以体现不同场景类型的
  差异化需求。
"""

from typing import Dict, List, Optional, Any, Callable, Tuple
from dataclasses import dataclass, field
from enum import Enum
import time
import uuid


class CalculatorState(Enum):
    IDLE = "idle"
    CALCULATING = "calculating"
    SYSTEM_PAUSED = "system_paused"


@dataclass
class VValueRequest:
    entry_id: str = ""
    task_type: str = ""
    source_slot_id: str = ""
    associated_behaviors: List[str] = field(default_factory=list)
    explicit_feedback: Optional[str] = None
    result_label: str = ""
    interaction_duration: float = 0.0
    behavior_params: Dict[str, Any] = field(default_factory=dict)
    user_id: str = ""
    need_preference_assist: bool = False
    timestamp: float = field(default_factory=time.time)


@dataclass
class UserPreferenceBaseline:
    user_id: str = ""
    preference_keywords: List[str] = field(default_factory=list)
    high_freq_tools: List[str] = field(default_factory=list)
    scene_distribution: Dict[str, float] = field(default_factory=dict)


@dataclass
class VValueResult:
    entry_id: str = ""
    v_value: float = 0.0
    triggered_signals: List[str] = field(default_factory=list)
    preference_matched: bool = False


@dataclass
class VValueStatusReport:
    state: str = ""
    recent_high_v_count: int = 0
    avg_v_value: float = 0.0


class VValueCalculator:
    # 用户价值信号权重配置（只读）
    SIGNAL_WEIGHTS = {
        "explicit_positive": 0.40,
        "explicit_negative": 0.20,
        "high_frequency": 0.25,
        "deep_interaction": 0.10,
        "task_quality": 0.05,
    }
    # 各信号基准分
    BASE_SCORES = {
        "explicit_positive": 0.90,
        "explicit_negative": -0.50,
        "high_frequency": 0.70,
        "deep_interaction": 0.55,
        "task_quality": 0.40,
    }
    # 高频复用阈值（7日内调用次数）
    HIGH_FREQ_THRESHOLD = 3
    # 深度交互：查看时长超标比例
    VIEW_DURATION_RATIO = 1.5
    # 连续跳过阈值
    CONSECUTIVE_SKIP_THRESHOLD = 3
    # 重试阈值
    RETRY_THRESHOLD = 2

    # 快速判定值
    EXPLICIT_LIKE_AND_SUCCESS_V = 0.90
    EXPLICIT_DISLIKE_V = 0.05
    FIRST_OCCASIONAL_V = 0.20

    # V值下边界（安全约束V-03：最低0.05）
    V_VALUE_MIN = 0.05

    # 分槽专属V值调整系数
    SLOT_V_ADJUST = {
        "ag-mem-15": 1.2,
        "ag-mem-16": 1.0,
        "ag-mem-17": 1.0,
        "ag-mem-18": 1.1,
        "ag-mem-19": 1.0,
    }

    # 用户偏好修正
    PREFERENCE_MATCH_MIN_RATIO = 0.5
    PREFERENCE_BOOST_MIN = 0.05
    PREFERENCE_BOOST_MAX = 0.15

    STATUS_REPORT_INTERVAL_SEC = 60

    def __init__(self):
        self.module_id = "ag-mem-32"
        self.module_name = "风格匹配度V值计算单元"
        self.version = "V1.0"

        self.state = CalculatorState.IDLE
        self._high_v_count: int = 0
        self._v_sum: float = 0.0
        self._v_count: int = 0
        self._last_status_time: float = 0.0
        self._pending_logs: List[Dict[str, Any]] = []

        # 回调注入
        self._query_v_value_request = None
        self._query_user_preference = None
        self._query_weight_config = None

        self._publish_v_value_result = None
        self._publish_status_report = None
        self._publish_event_log = None

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成, V值下边界={self.V_VALUE_MIN}")

    # ========== 回调注入 ==========
    def set_v_value_request_query(self, callback: Callable[[], Optional[VValueRequest]]):
        self._query_v_value_request = callback

    def set_user_preference_query(self, callback: Callable[[], Optional[UserPreferenceBaseline]]):
        self._query_user_preference = callback

    def set_weight_config_query(self, callback: Callable[[], Optional[Dict[str, Any]]]):
        self._query_weight_config = callback

    def set_v_value_result_publisher(self, callback: Callable[[VValueResult], None]):
        self._publish_v_value_result = callback

    def set_status_report_publisher(self, callback: Callable[[VValueStatusReport], None]):
        self._publish_status_report = callback

    def set_event_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_event_log = callback

    # ========== 主循环 ==========
    def run_calculation_cycle(self) -> Optional[VValueResult]:
        now = time.time()

        if self.state == CalculatorState.SYSTEM_PAUSED:
            return None

        # 定期状态上报
        if now - self._last_status_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._publish_status()
            self._last_status_time = now

        request = self._query_v_value_request() if self._query_v_value_request else None
        if request is None:
            return None

        self.state = CalculatorState.CALCULATING
        result = self._calculate_v_value(request)
        self.state = CalculatorState.IDLE

        if self._publish_v_value_result:
            self._publish_v_value_result(result)

        # 统计
        if result.v_value >= 0.70:
            self._high_v_count += 1
        self._v_sum += result.v_value
        self._v_count += 1

        return result

    # ========== 核心计算 ==========
    def _calculate_v_value(self, request: VValueRequest) -> VValueResult:
        # 快速判定1：显式正向反馈 + 成功
        # 注意：快速判定路径不受分槽调整系数影响，详见模块docstring设计说明
        if request.explicit_feedback in ("点赞", "收藏", "标记有用", "要求记住") and request.result_label == "成功":
            return VValueResult(
                entry_id=request.entry_id,
                v_value=self.EXPLICIT_LIKE_AND_SUCCESS_V,
                triggered_signals=["显式正向反馈+成功"],
                preference_matched=False
            )

        # 快速判定2：显式负向反馈
        if request.explicit_feedback in ("点踩", "删除", "标记无用", "要求忽略"):
            return VValueResult(
                entry_id=request.entry_id,
                v_value=self.EXPLICIT_DISLIKE_V,
                triggered_signals=["显式负向反馈"],
                preference_matched=False
            )

        # 快速判定3：首次偶发行为
        if not request.associated_behaviors and request.explicit_feedback is None:
            return VValueResult(
                entry_id=request.entry_id,
                v_value=self.FIRST_OCCASIONAL_V,
                triggered_signals=["首次偶发行为"],
                preference_matched=False
            )

        # 标准计算
        v_value = 0.0
        triggered_signals = []

        # 信号1：显式正向反馈
        if request.explicit_feedback in ("点赞", "收藏", "标记有用"):
            v_value += self.BASE_SCORES["explicit_positive"] * self.SIGNAL_WEIGHTS["explicit_positive"]
            triggered_signals.append(f"显式正向反馈:{request.explicit_feedback}")

        # 信号2：显式负向反馈（负分）
        if request.explicit_feedback in ("点踩", "删除", "标记无用"):
            v_value += self.BASE_SCORES["explicit_negative"] * self.SIGNAL_WEIGHTS["explicit_negative"]
            triggered_signals.append(f"显式负向反馈:{request.explicit_feedback}")

        # 信号3：高频复用
        reuse_count = self._count_recent_reuse(request.associated_behaviors, request.task_type)
        if reuse_count >= self.HIGH_FREQ_THRESHOLD:
            reuse_intensity = min(reuse_count / 5.0, 1.0)
            v_value += self.BASE_SCORES["high_frequency"] * reuse_intensity * self.SIGNAL_WEIGHTS["high_frequency"]
            triggered_signals.append(f"高频复用:{reuse_count}次")

        # 信号4：深度交互
        deep_score = 0.0
        if request.behavior_params.get("is_copy"):
            deep_score += 0.5
            triggered_signals.append("复制结果")
        if request.behavior_params.get("is_share"):
            deep_score += 0.5
            triggered_signals.append("分享结果")
        if request.interaction_duration > 0 and request.behavior_params.get("historical_avg_duration", 0) > 0:
            if request.interaction_duration > request.behavior_params["historical_avg_duration"] * self.VIEW_DURATION_RATIO:
                deep_score += 0.5
                triggered_signals.append("长时间查看")
        v_value += self.BASE_SCORES["deep_interaction"] * deep_score * self.SIGNAL_WEIGHTS["deep_interaction"]

        # 信号5：任务完成质量
        if request.result_label == "成功" and request.behavior_params.get("retry_count", 0) == 0:
            v_value += self.BASE_SCORES["task_quality"] * self.SIGNAL_WEIGHTS["task_quality"]
            triggered_signals.append("一次成功")

        # 应用分槽专属权重调整
        slot_adjust = self.SLOT_V_ADJUST.get(request.source_slot_id, 1.0)
        v_value *= slot_adjust

        # 用户偏好修正（可选）
        preference_matched = False
        if request.need_preference_assist and request.user_id:
            baseline = self._query_user_preference() if self._query_user_preference else None
            if baseline:
                task_keywords = self._extract_task_keywords(request)
                match_ratio = self._calculate_keyword_match(task_keywords, baseline.preference_keywords)
                if match_ratio > self.PREFERENCE_MATCH_MIN_RATIO:
                    boost = self.PREFERENCE_BOOST_MIN + match_ratio * (self.PREFERENCE_BOOST_MAX - self.PREFERENCE_BOOST_MIN)
                    v_value += boost
                    triggered_signals.append(f"用户偏好匹配:{match_ratio:.2f}")
                    preference_matched = True

        # 边界裁剪（安全约束V-03：下边界0.05）
        v_value = max(self.V_VALUE_MIN, min(1.0, round(v_value, 2)))

        return VValueResult(
            entry_id=request.entry_id,
            v_value=v_value,
            triggered_signals=triggered_signals,
            preference_matched=preference_matched
        )

    # ========== 辅助方法 ==========
    def _count_recent_reuse(self, behaviors: List[str], task_type: str) -> int:
        count = 0
        for b in behaviors:
            if task_type in b:
                count += 1
        return count

    def _extract_task_keywords(self, request: VValueRequest) -> List[str]:
        keywords = []
        keywords.append(request.task_type)
        if request.behavior_params.get("tool_name"):
            keywords.append(request.behavior_params["tool_name"])
        if request.behavior_params.get("task_keywords"):
            if isinstance(request.behavior_params["task_keywords"], list):
                keywords.extend(request.behavior_params["task_keywords"])
        return keywords

    def _calculate_keyword_match(self, task_keywords: List[str], preference_keywords: List[str]) -> float:
        if not preference_keywords:
            return 0.0
        match_count = sum(1 for kw in task_keywords if kw in preference_keywords)
        return match_count / max(len(task_keywords), 1)

    def _publish_status(self):
        avg = self._v_sum / max(self._v_count, 1)
        if self._publish_status_report:
            self._publish_status_report(VValueStatusReport(
                state=self.state.value,
                recent_high_v_count=self._high_v_count,
                avg_v_value=round(avg, 3)
            ))

    def get_state(self) -> CalculatorState:
        return self.state

    def emergency_shutdown(self):
        self.state = CalculatorState.SYSTEM_PAUSED
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
    print("  Agent-mlnf-mem 风格匹配度V值计算单元 (ag-mem-32) 演示")
    print("=" * 70)

    calc = VValueCalculator()

    print_separator("STEP 1: 显式点赞+成功 → V=0.90")
    calc.set_v_value_request_query(lambda: VValueRequest(
        entry_id="E01", task_type="对话交互", source_slot_id="ag-mem-15",
        explicit_feedback="点赞", result_label="成功"
    ))
    result = calc.run_calculation_cycle()
    if result:
        print(f"  V值={result.v_value:.2f}, 信号={result.triggered_signals}")

    print_separator("STEP 2: 显式点踩 → V=0.05")
    calc.set_v_value_request_query(lambda: VValueRequest(
        entry_id="E02", task_type="工具调用", source_slot_id="ag-mem-16",
        explicit_feedback="点踩", result_label="失败"
    ))
    result = calc.run_calculation_cycle()
    if result:
        print(f"  V值={result.v_value:.2f}, 信号={result.triggered_signals}")

    print_separator("STEP 3: 高频复用+深度交互 → 较高V值")
    calc.set_v_value_request_query(lambda: VValueRequest(
        entry_id="E03", task_type="信息检索", source_slot_id="ag-mem-17",
        associated_behaviors=["search", "search", "search", "search"],
        behavior_params={"is_copy": True, "is_share": False},
        result_label="成功"
    ))
    result = calc.run_calculation_cycle()
    if result:
        print(f"  V值={result.v_value:.2f}, 信号={result.triggered_signals}")

    print_separator("STEP 4: 显式负向+无正面信号 → V≥0.05 (修复验证)")
    calc.set_v_value_request_query(lambda: VValueRequest(
        entry_id="E04", task_type="工具调用", source_slot_id="ag-mem-16",
        explicit_feedback="点踩",
        associated_behaviors=[],
        behavior_params={},
        result_label="失败"
    ))
    result = calc.run_calculation_cycle()
    if result:
        print(f"  V值={result.v_value:.2f}, 信号={result.triggered_signals} (应≥0.05)")

    print("\n✅ 风格匹配度V值计算单元演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("ag-mem-32 风格匹配度V值计算单元 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        def setup_calc():
            return VValueCalculator()

        # TC-M32-01: 点赞+成功 → V=0.90
        print("\n[TC-M32-01] 点赞+成功 → V=0.90")
        try:
            c = setup_calc()
            c.set_v_value_request_query(lambda: VValueRequest(
                entry_id="T01", task_type="对话交互", source_slot_id="ag-mem-15",
                explicit_feedback="点赞", result_label="成功"
            ))
            result = c.run_calculation_cycle()
            assert result.v_value == 0.90
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M32-02: 点踩 → V=0.05
        print("\n[TC-M32-02] 点踩 → V=0.05")
        try:
            c = setup_calc()
            c.set_v_value_request_query(lambda: VValueRequest(
                entry_id="T02", task_type="工具调用", source_slot_id="ag-mem-16",
                explicit_feedback="点踩", result_label="失败"
            ))
            result = c.run_calculation_cycle()
            assert result.v_value == 0.05
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M32-03: 首次偶发行为 → V=0.20
        print("\n[TC-M32-03] 首次偶发行为 → V=0.20")
        try:
            c = setup_calc()
            c.set_v_value_request_query(lambda: VValueRequest(
                entry_id="T03", task_type="信息检索", source_slot_id="ag-mem-17",
                associated_behaviors=[], explicit_feedback=None, result_label="成功"
            ))
            result = c.run_calculation_cycle()
            assert result.v_value == 0.20
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M32-04: 高频复用+深度交互 → V值较高
        print("\n[TC-M32-04] 高频复用+深度交互 → V值较高")
        try:
            c = setup_calc()
            c.set_v_value_request_query(lambda: VValueRequest(
                entry_id="T04", task_type="信息检索", source_slot_id="ag-mem-17",
                associated_behaviors=["search", "search", "search", "search"],
                behavior_params={"is_copy": True},
                result_label="成功"
            ))
            result = c.run_calculation_cycle()
            assert result.v_value >= 0.30
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M32-05: 显式负向+无正面信号 → V≥0.05 (修复验证)
        print("\n[TC-M32-05] 显式负向+无正面信号 → V≥0.05")
        try:
            c = setup_calc()
            c.set_v_value_request_query(lambda: VValueRequest(
                entry_id="T05", task_type="工具调用", source_slot_id="ag-mem-16",
                explicit_feedback="点踩",
                associated_behaviors=[],
                behavior_params={},
                result_label="失败"
            ))
            result = c.run_calculation_cycle()
            assert result.v_value == 0.05
            assert result.v_value >= 0.05  # 安全约束V-03
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M32-06: 紧急熔断
        print("\n[TC-M32-06] 紧急熔断")
        try:
            c = setup_calc()
            c.emergency_shutdown()
            assert c.state == CalculatorState.SYSTEM_PAUSED
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