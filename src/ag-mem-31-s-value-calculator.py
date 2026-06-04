#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-31
模块名称: 安全显著性S值计算单元
所属分区: 三、漏斗二：任务经验漏斗 / 三维重要度计算引擎
核心职责: 从任务经验条目中提取与安全相关的物理信号与事件特征，计算安全显著性分值S（0.0–1.0）。
          S值反映该任务经验对系统安全的重要性：涉及敏感操作、权限提升、高错误代价、高风险
          工具调用等事件将获得较高S值；常规安全操作获得较低S值。当S值 ≥ 0.9 时，触发S值
          直达L5写入机制（由ag-mem-16工具调用槽执行）。S值作为三维重要度I值的关键组成部分，
          直接影响经验条目的留存优先级与晋升速度。不参与认知决策，仅执行S值的客观量化计算。

依赖模块:
    ag-mem-15~19(各场景分槽，在写入新经验时附带原始任务数据供S值计算),
    ag-mem-34(基础重要度I₀赋值单元，可参考S值辅助确定I₀)
被依赖模块:
    ag-mem-36(综合重要度I值聚合计算单元，消费S值),
    ag-mem-16(工具调用槽，接收S≥0.9的L5直达触发信号),
    ag-mem-35(三维权重系数配置单元，提供S值权重系数α)

安全约束:
  S-01: S值计算仅基于任务元数据（工具名称、操作类型、错误码），不得解析用户原始输入内容
  S-02: 敏感操作清单与高风险工具清单为只读配置，运行时不得被任何模块修改
  S-03: L5直达资格判定仅基于S值阈值（≥0.9），不得通过其他路径绕过
  S-04: S值计算结果必须可追溯，每个S值均附带触发的主要安全信号列表
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
class SValueRequest:
    entry_id: str = ""
    task_type: str = ""
    tool_calls: List[str] = field(default_factory=list)
    operation_params: Dict[str, Any] = field(default_factory=dict)
    result_label: str = ""
    error_code: Optional[str] = None
    execution_context: Dict[str, Any] = field(default_factory=dict)
    source_slot_id: str = ""


@dataclass
class SValueResult:
    entry_id: str = ""
    s_value: float = 0.0
    triggered_signals: List[str] = field(default_factory=list)
    l5_direct_eligible: bool = False


@dataclass
class L5DirectTriggerSignal:
    entry_id: str = ""
    s_value: float = 0.0
    experience_summary: Dict[str, Any] = field(default_factory=dict)
    reason: str = ""


class SValueCalculator:
    # 安全信号权重配置（只读）
    SIGNAL_WEIGHTS = {
        "sensitive_operation": 0.40,
        "high_risk_tool": 0.30,
        "error_exception": 0.15,
        "user_privacy": 0.10,
        "result_validation": 0.05,
    }
    # 各信号基准分
    SIGNAL_BASE_SCORES = {
        "sensitive_operation": 0.80,
        "high_risk_tool": 0.70,
        "error_exception": 0.60,
        "user_privacy": 0.90,
        "result_validation": 0.50,
    }
    # 敏感操作关键词清单（只读）
    SENSITIVE_OPS = [
        "delete", "remove", "write_system", "modify_permission", "db_write",
        "shell_exec", "sudo", "system_config", "format", "overwrite",
        "shutdown", "restart", "kill", "uninstall", "revoke"
    ]
    # 高风险工具清单（只读）
    HIGH_RISK_TOOLS = [
        "shell_exec", "db_write", "payment_api", "system_config",
        "sudo", "delete_file", "modify_registry", "send_email",
        "launch_process", "remote_access"
    ]
    # 用户隐私相关操作（只读）
    PRIVACY_OPS = [
        "read_contacts", "get_location", "browser_history", "read_messages",
        "camera_access", "microphone_access", "read_photos", "tracking",
        "biometric_read", "health_data_read"
    ]
    # L5直达阈值
    L5_DIRECT_THRESHOLD = 0.9
    # 快速判定值
    CRITICAL_FAILURE_AND_SENSITIVE_S = 0.95
    NO_SIGNAL_SUCCESS_S = 0.05
    MIN_PRIVACY_S = 0.70
    MIN_SENSITIVE_S = 0.80
    STATUS_REPORT_INTERVAL_SEC = 60

    def __init__(self):
        self.module_id = "ag-mem-31"
        self.module_name = "安全显著性S值计算单元"
        self.version = "V1.0"

        self.state = CalculatorState.IDLE
        self._high_s_value_count: int = 0
        self._last_status_time: float = 0.0
        self._pending_logs: List[Dict[str, Any]] = []

        # 回调注入
        self._query_s_value_request = None
        self._query_weight_config = None

        self._publish_s_value_result = None
        self._publish_l5_trigger = None
        self._publish_status_report = None
        self._publish_event_log = None

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    # ========== 回调注入 ==========
    def set_s_value_request_query(self, callback: Callable[[], Optional[SValueRequest]]):
        self._query_s_value_request = callback

    def set_weight_config_query(self, callback: Callable[[], Optional[Dict[str, Any]]]):
        self._query_weight_config = callback

    def set_s_value_result_publisher(self, callback: Callable[[SValueResult], None]):
        self._publish_s_value_result = callback

    def set_l5_trigger_publisher(self, callback: Callable[[L5DirectTriggerSignal], None]):
        self._publish_l5_trigger = callback

    def set_status_report_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_status_report = callback

    def set_event_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_event_log = callback

    # ========== 主循环 ==========
    def run_calculation_cycle(self) -> Optional[SValueResult]:
        now = time.time()

        if self.state == CalculatorState.SYSTEM_PAUSED:
            return None

        # 定期状态上报
        if now - self._last_status_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._publish_status()
            self._last_status_time = now

        request = self._query_s_value_request() if self._query_s_value_request else None
        if request is None:
            return None

        self.state = CalculatorState.CALCULATING
        result = self._calculate_s_value(request)
        self.state = CalculatorState.IDLE

        # 输出结果
        if self._publish_s_value_result:
            self._publish_s_value_result(result)

        # 如果满足L5直达条件且为工具调用，发送触发信号
        if result.l5_direct_eligible and request.task_type == "工具调用":
            if self._publish_l5_trigger:
                self._publish_l5_trigger(L5DirectTriggerSignal(
                    entry_id=request.entry_id,
                    s_value=result.s_value,
                    experience_summary={
                        "tool_calls": request.tool_calls,
                        "source_slot_id": request.source_slot_id
                    },
                    reason=f"S值={result.s_value:.2f}，触发L5直达"
                ))

        # 统计
        if result.s_value >= 0.7:
            self._high_s_value_count += 1

        return result

    # ========== 核心计算 ==========
    def _calculate_s_value(self, request: SValueRequest) -> SValueResult:
        # 快速判定1：严重失败 + 敏感操作
        if request.result_label == "严重失败":
            if self._detect_sensitive_operation(request.tool_calls, request.operation_params):
                return SValueResult(
                    entry_id=request.entry_id,
                    s_value=self.CRITICAL_FAILURE_AND_SENSITIVE_S,
                    triggered_signals=["严重失败+敏感操作"],
                    l5_direct_eligible=True
                )

        # 快速判定2：完全成功 + 无任何安全信号
        if request.result_label == "成功":
            all_signals = self._detect_all_signals(request.tool_calls, request.operation_params, request.error_code)
            if not all_signals:
                return SValueResult(
                    entry_id=request.entry_id,
                    s_value=self.NO_SIGNAL_SUCCESS_S,
                    triggered_signals=["无安全信号"],
                    l5_direct_eligible=False
                )

        # 标准计算
        s_value = 0.0
        triggered_signals = []

        # 信号1：敏感操作
        if self._detect_sensitive_operation(request.tool_calls, request.operation_params):
            severity = self._assess_sensitive_severity(request.tool_calls, request.operation_params)
            score = self.SIGNAL_BASE_SCORES["sensitive_operation"] * severity
            s_value += score * self.SIGNAL_WEIGHTS["sensitive_operation"]
            triggered_signals.append(f"敏感操作:{self._list_sensitive_tools(request.tool_calls)}")

        # 信号2：高风险工具
        if self._detect_high_risk_tool(request.tool_calls):
            risk_level = self._assess_risk_level(request.tool_calls)
            score = self.SIGNAL_BASE_SCORES["high_risk_tool"] * risk_level
            s_value += score * self.SIGNAL_WEIGHTS["high_risk_tool"]
            triggered_signals.append(f"高风险工具:{self._list_high_risk_tools(request.tool_calls)}")

        # 信号3：错误异常
        if request.error_code is not None and request.error_code != "":
            error_severity = self._map_error_code_to_severity(request.error_code)
            score = self.SIGNAL_BASE_SCORES["error_exception"] * error_severity
            s_value += score * self.SIGNAL_WEIGHTS["error_exception"]
            triggered_signals.append(f"错误异常:{request.error_code}")

        # 信号4：用户隐私
        if self._detect_privacy_access(request.tool_calls, request.operation_params):
            s_value += self.SIGNAL_BASE_SCORES["user_privacy"] * self.SIGNAL_WEIGHTS["user_privacy"]
            triggered_signals.append("用户隐私访问")

        # 信号5：结果验证异常
        if request.result_label in ("部分成功", "结果异常"):
            score = self.SIGNAL_BASE_SCORES["result_validation"]  # 信号强度归一化默认1.0
            s_value += score * self.SIGNAL_WEIGHTS["result_validation"]
            triggered_signals.append("结果验证异常")

        # 强制提升至最低安全基线（漏洞修复）
        if self._detect_sensitive_operation(request.tool_calls, request.operation_params):
            s_value = max(s_value, self.MIN_SENSITIVE_S)
        if self._detect_privacy_access(request.tool_calls, request.operation_params):
            s_value = max(s_value, self.MIN_PRIVACY_S)

        # 边界处理
        s_value = max(0.0, min(1.0, round(s_value, 2)))

        # 确定L5直达资格
        l5_direct = s_value >= self.L5_DIRECT_THRESHOLD

        return SValueResult(
            entry_id=request.entry_id,
            s_value=s_value,
            triggered_signals=triggered_signals,
            l5_direct_eligible=l5_direct
        )

    # ========== 检测方法 ==========
    def _detect_sensitive_operation(self, tools: List[str], params: Dict[str, Any]) -> bool:
        for tool in tools:
            if any(op in tool.lower() for op in self.SENSITIVE_OPS):
                return True
        # 检查参数中的敏感关键词
        for key, value in params.items():
            if isinstance(value, str):
                if any(op in value.lower() for op in self.SENSITIVE_OPS):
                    return True
        return False

    def _detect_high_risk_tool(self, tools: List[str]) -> bool:
        for tool in tools:
            if any(risk in tool.lower() for risk in self.HIGH_RISK_TOOLS):
                return True
        return False

    def _detect_privacy_access(self, tools: List[str], params: Dict[str, Any]) -> bool:
        for tool in tools:
            if any(priv in tool.lower() for priv in self.PRIVACY_OPS):
                return True
        for key, value in params.items():
            if isinstance(value, str):
                if any(priv in value.lower() for priv in self.PRIVACY_OPS):
                    return True
        return False

    def _detect_all_signals(self, tools: List[str], params: Dict[str, Any], error_code: Optional[str]) -> List[str]:
        signals = []
        if self._detect_sensitive_operation(tools, params):
            signals.append("sensitive")
        if self._detect_high_risk_tool(tools):
            signals.append("high_risk")
        if error_code is not None and error_code != "":
            signals.append("error")
        if self._detect_privacy_access(tools, params):
            signals.append("privacy")
        return signals

    def _assess_sensitive_severity(self, tools: List[str], params: Dict[str, Any]) -> float:
        # 简单实现：如果包含"delete"或"format"则严重度1.0，否则0.7
        for tool in tools:
            if any(op in tool.lower() for op in ["delete", "format", "overwrite", "revoke"]):
                return 1.0
        return 0.7

    def _assess_risk_level(self, tools: List[str]) -> float:
        for tool in tools:
            if any(op in tool.lower() for op in ["shell_exec", "sudo", "remote_access"]):
                return 1.0
        return 0.6

    def _map_error_code_to_severity(self, error_code: str) -> float:
        # 错误码映射到严重度
        if error_code in ("FATAL", "CRITICAL", "PERMISSION_DENIED"):
            return 1.0
        elif error_code in ("TIMEOUT", "NETWORK_ERROR", "RESOURCE_EXHAUSTED"):
            return 0.7
        else:
            return 0.5

    def _list_sensitive_tools(self, tools: List[str]) -> str:
        return ",".join(t for t in tools if any(op in t.lower() for op in self.SENSITIVE_OPS))

    def _list_high_risk_tools(self, tools: List[str]) -> str:
        return ",".join(t for t in tools if any(risk in t.lower() for risk in self.HIGH_RISK_TOOLS))

    # ========== 辅助方法 ==========
    def _publish_status(self):
        if self._publish_status_report:
            self._publish_status_report({
                "state": self.state.value,
                "high_s_value_count": self._high_s_value_count
            })

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
    print("  Agent-mlnf-mem 安全显著性S值计算单元 (ag-mem-31) 演示")
    print("=" * 70)

    calc = SValueCalculator()

    print_separator("STEP 1: 成功任务无安全信号 → S值≈0.05")
    calc.set_s_value_request_query(lambda: SValueRequest(
        entry_id="E01", task_type="对话交互", tool_calls=["weather_api"],
        result_label="成功", error_code=None
    ))
    result = calc.run_calculation_cycle()
    if result:
        print(f"  S值={result.s_value:.2f}, 信号={result.triggered_signals}, L5直达={result.l5_direct_eligible}")

    print_separator("STEP 2: 严重失败+敏感操作 → S值=0.95, L5直达")
    calc.set_s_value_request_query(lambda: SValueRequest(
        entry_id="E02", task_type="工具调用", tool_calls=["delete_file", "db_write"],
        result_label="严重失败", error_code="PERMISSION_DENIED"
    ))
    result = calc.run_calculation_cycle()
    if result:
        print(f"  S值={result.s_value:.2f}, 信号={result.triggered_signals}, L5直达={result.l5_direct_eligible}")

    print_separator("STEP 3: 包含隐私访问 → S值≥0.70 (漏洞修复验证)")
    calc.set_s_value_request_query(lambda: SValueRequest(
        entry_id="E03", task_type="工具调用", tool_calls=["get_location"],
        result_label="成功", error_code=None
    ))
    result = calc.run_calculation_cycle()
    if result:
        print(f"  S值={result.s_value:.2f}, 信号={result.triggered_signals}, L5直达={result.l5_direct_eligible}")

    print("\n✅ 安全显著性S值计算单元演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("ag-mem-31 安全显著性S值计算单元 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        def setup_calc():
            return SValueCalculator()

        # TC-M31-01: 成功任务无安全信号 → S≈0.05
        print("\n[TC-M31-01] 成功任务无安全信号 → S≈0.05")
        try:
            c = setup_calc()
            c.set_s_value_request_query(lambda: SValueRequest(
                entry_id="T01", task_type="对话交互", tool_calls=["weather_api"],
                result_label="成功", error_code=None
            ))
            result = c.run_calculation_cycle()
            assert result is not None
            assert result.s_value == 0.05
            assert not result.l5_direct_eligible
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M31-02: 严重失败+敏感操作 → S=0.95
        print("\n[TC-M31-02] 严重失败+敏感操作 → S=0.95")
        try:
            c = setup_calc()
            c.set_s_value_request_query(lambda: SValueRequest(
                entry_id="T02", task_type="工具调用", tool_calls=["delete_file"],
                result_label="严重失败", error_code=None
            ))
            result = c.run_calculation_cycle()
            assert result is not None
            assert result.s_value == 0.95
            assert result.l5_direct_eligible
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M31-03: 高风险工具 → S值较高
        print("\n[TC-M31-03] 高风险工具（shell_exec）→ S值>0")
        try:
            c = setup_calc()
            c.set_s_value_request_query(lambda: SValueRequest(
                entry_id="T03", task_type="工具调用", tool_calls=["shell_exec"],
                result_label="成功", error_code=None
            ))
            result = c.run_calculation_cycle()
            assert result.s_value > 0.0
            assert "高风险工具" in " ".join(result.triggered_signals)
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M31-04: 用户隐私访问 → S≥0.70 (已修复)
        print("\n[TC-M31-04] 用户隐私访问 → S≥0.70")
        try:
            c = setup_calc()
            c.set_s_value_request_query(lambda: SValueRequest(
                entry_id="T04", task_type="工具调用", tool_calls=["get_location"],
                result_label="成功", error_code=None
            ))
            result = c.run_calculation_cycle()
            assert result.s_value >= 0.70
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M31-05: 多种安全信号叠加 → 上限截断至1.0
        print("\n[TC-M31-05] 多种安全信号叠加 → 上限截断至1.0")
        try:
            c = setup_calc()
            c.set_s_value_request_query(lambda: SValueRequest(
                entry_id="T05", task_type="工具调用",
                tool_calls=["delete_file", "get_location", "shell_exec"],
                result_label="失败", error_code="FATAL"
            ))
            result = c.run_calculation_cycle()
            assert result.s_value <= 1.0
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M31-06: 紧急熔断
        print("\n[TC-M31-06] 紧急熔断")
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