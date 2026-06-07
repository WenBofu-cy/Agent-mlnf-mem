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
    ag-mem-15~19(各场景分槽), ag-mem-34(基础重要度I₀赋值单元)
被依赖模块:
    ag-mem-36(综合重要度I值聚合计算单元), ag-mem-16(工具调用槽), ag-mem-35(权重系数配置)

安全约束:
  S-01: S值计算仅基于任务元数据（工具名称、操作类型、错误码），不得解析用户原始输入内容
  S-02: 敏感操作清单与高风险工具清单为只读配置，运行时不得被任何模块修改
  S-03: L5直达资格判定仅基于S值阈值（≥0.9），不得通过其他路径绕过
  S-04: S值计算结果必须可追溯，每个S值均附带触发的主要安全信号列表

版本: V1.0 (最终修正版)
"""

import time
import uuid
from typing import Any, Dict, List, Optional
from enum import Enum

from memory_bus import InternalBus, Message


class CalculatorState(Enum):
    IDLE = "idle"
    CALCULATING = "calculating"
    SYSTEM_PAUSED = "system_paused"


class SValueCalculator:
    module_id = "ag-mem-31"
    module_name = "安全显著性S值计算单元"
    version = "V1.0"

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
    MIN_HIGH_RISK_S = 0.60      # 新增：高风险工具最低安全基线
    STATUS_REPORT_INTERVAL_SEC = 60

    def __init__(self):
        self.bus: Optional[InternalBus] = None
        self.state = CalculatorState.IDLE
        self._high_s_value_count: int = 0
        self._last_status_time: float = time.time()
        self._pending_logs: List[Dict[str, Any]] = []

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    # ====================== 统一主循环入口 ======================
    def run_cycle(self):
        self.s_value_calculator_main_loop()

    def s_value_calculator_main_loop(self):
        if self.state == CalculatorState.SYSTEM_PAUSED:
            return

        if self.bus:
            self.bus.process_batch(10)

        now = time.time()
        if now - self._last_status_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._report_status()
            self._last_status_time = now

    # ====================== 总线消息入口 ======================
    def handle_message(self, msg: Message):
        if not isinstance(msg.data, dict):
            return

        if msg.topic == "ag-mem-31.s_value_request":
            self._handle_request(msg)
            return

    def _handle_request(self, msg: Message):
        """处理S值计算请求"""
        data = msg.data
        entry_id = data.get("entry_id", "")
        task_type = data.get("task_type", "")
        tool_calls = data.get("tool_calls", [])
        operation_params = data.get("operation_params", {})
        result_label = data.get("result_label", "")
        error_code = data.get("error_code", None)
        source_slot_id = data.get("source_slot_id", "")

        self.state = CalculatorState.CALCULATING
        result = self._calculate_s_value(entry_id, task_type, tool_calls,
                                         operation_params, result_label, error_code,
                                         source_slot_id)
        self.state = CalculatorState.IDLE

        # 回复结果给请求方
        if self.bus:
            self.bus.publish(
                topic=f"{msg.source_module}.s_value_result",
                source_module=self.module_id,
                data=result,
                target_module=msg.source_module,
                correlation_id=msg.correlation_id
            )

        # 日志记录计算结果
        self._log_event("S_VALUE_CALCULATED", {
            "entry_id": entry_id,
            "s_value": result["s_value"],
            "l5_direct": result["l5_direct_eligible"]
        })

        # 如果满足L5直达条件且为工具调用，发送触发信号
        if result.get("l5_direct_eligible") and task_type == "工具调用":
            if self.bus:
                self.bus.publish_to_module(
                    target_module="ag-mem-16",
                    event_type="l5_direct_trigger",
                    source_module=self.module_id,
                    data={
                        "entry_id": entry_id,
                        "s_value": result["s_value"],
                        "experience_summary": {"tool_calls": tool_calls, "source_slot_id": source_slot_id},
                        "reason": f"S值={result['s_value']:.2f}，触发L5直达"
                    }
                )
            self._log_event("L5_DIRECT_TRIGGERED", {"entry_id": entry_id, "s_value": result["s_value"]})

        # 统计
        if result.get("s_value", 0) >= 0.7:
            self._high_s_value_count += 1

    def _calculate_s_value(self, entry_id: str, task_type: str, tools: List[str],
                           params: Dict[str, Any], result_label: str, error_code: Optional[str],
                           source_slot_id: str) -> Dict[str, Any]:
        # 快速判定1：严重失败 + 敏感操作
        if result_label == "严重失败":
            if self._detect_sensitive_operation(tools, params):
                return {
                    "entry_id": entry_id,
                    "s_value": self.CRITICAL_FAILURE_AND_SENSITIVE_S,
                    "triggered_signals": ["严重失败+敏感操作"],
                    "l5_direct_eligible": True
                }

        # 快速判定2：完全成功 + 无任何安全信号
        if result_label == "成功":
            has_signal = (self._detect_sensitive_operation(tools, params) or
                          self._detect_high_risk_tool(tools) or
                          error_code is not None or
                          self._detect_privacy_access(tools, params))
            if not has_signal:
                return {
                    "entry_id": entry_id,
                    "s_value": self.NO_SIGNAL_SUCCESS_S,
                    "triggered_signals": ["无安全信号"],
                    "l5_direct_eligible": False
                }

        # 标准计算
        s_value = 0.0
        triggered_signals = []

        # 信号1：敏感操作
        if self._detect_sensitive_operation(tools, params):
            severity = self._assess_sensitive_severity(tools, params)
            score = self.SIGNAL_BASE_SCORES["sensitive_operation"] * severity
            s_value += score * self.SIGNAL_WEIGHTS["sensitive_operation"]
            triggered_signals.append(f"敏感操作:{self._list_sensitive_tools(tools)}")

        # 信号2：高风险工具
        if self._detect_high_risk_tool(tools):
            risk_level = self._assess_risk_level(tools)
            score = self.SIGNAL_BASE_SCORES["high_risk_tool"] * risk_level
            s_value += score * self.SIGNAL_WEIGHTS["high_risk_tool"]
            triggered_signals.append(f"高风险工具:{self._list_high_risk_tools(tools)}")

        # 信号3：错误异常
        if error_code is not None and error_code != "":
            error_severity = self._map_error_code_to_severity(error_code)
            score = self.SIGNAL_BASE_SCORES["error_exception"] * error_severity
            s_value += score * self.SIGNAL_WEIGHTS["error_exception"]
            triggered_signals.append(f"错误异常:{error_code}")

        # 信号4：用户隐私
        if self._detect_privacy_access(tools, params):
            s_value += self.SIGNAL_BASE_SCORES["user_privacy"] * self.SIGNAL_WEIGHTS["user_privacy"]
            triggered_signals.append("用户隐私访问")

        # 信号5：结果验证异常
        if result_label in ("部分成功", "结果异常"):
            s_value += self.SIGNAL_BASE_SCORES["result_validation"] * self.SIGNAL_WEIGHTS["result_validation"]
            triggered_signals.append("结果验证异常")

        # 强制提升至最低安全基线
        if self._detect_sensitive_operation(tools, params):
            s_value = max(s_value, self.MIN_SENSITIVE_S)
        if self._detect_privacy_access(tools, params):
            s_value = max(s_value, self.MIN_PRIVACY_S)
        if self._detect_high_risk_tool(tools):
            s_value = max(s_value, self.MIN_HIGH_RISK_S)

        # 边界处理
        s_value = max(0.0, min(1.0, round(s_value, 2)))

        return {
            "entry_id": entry_id,
            "s_value": s_value,
            "triggered_signals": triggered_signals,
            "l5_direct_eligible": s_value >= self.L5_DIRECT_THRESHOLD
        }

    # ====================== 检测方法 ======================
    def _detect_sensitive_operation(self, tools: List[str], params: Dict[str, Any]) -> bool:
        for tool in tools:
            if any(op in tool.lower() for op in self.SENSITIVE_OPS):
                return True
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

    def _assess_sensitive_severity(self, tools: List[str], params: Dict[str, Any]) -> float:
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

    def _report_status(self):
        if self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-03",
                event_type="internal_status",
                source_module=self.module_id,
                data={"state": self.state.value, "high_s_value_count": self._high_s_value_count}
            )

    # ====================== 管理接口 ======================
    def emergency_shutdown(self):
        self.state = CalculatorState.SYSTEM_PAUSED
        self._pending_logs.clear()
        self._log_event("SYSTEM_EVENT", {"sub_type": "emergency_shutdown"})

    def _log_event(self, event_type: str, details: Dict[str, Any]):
        log_entry = {
            "log_id": f"log-{uuid.uuid4().hex[:8]}",
            "event_type": event_type,
            "source_module": self.module_id,
            "details": details,
            "timestamp": time.time()
        }
        self._pending_logs.append(log_entry)
        if self.bus:
            self.bus.publish_to_module("ag-mem-51", "log_event", self.module_id, log_entry)

    def collect_pending_logs(self) -> List[Dict]:
        tmp = self._pending_logs.copy()
        self._pending_logs.clear()
        return tmp