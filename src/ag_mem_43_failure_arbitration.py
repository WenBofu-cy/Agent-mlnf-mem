#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-43
模块名称: 失败经验安全仲裁三道校验单元
所属分区: 三、漏斗二：任务经验漏斗 / 晋升与遗忘执行机制
核心职责: 对结果标签为“失败”或“策略失误”且当前位于 L3 中期层的经验条目，在其晋升至 L4
          长期层之前，强制执行三道递进式安全仲裁校验。第一道：安全规则合规校验——向
          ag-mem-45（安全规则库）发起请求，验证工具调用与操作序列是否违反安全边界；
          第二道：任务逻辑一致性校验——检查任务目标、工具选择与执行结果之间是否存在不可
          调和的逻辑冲突；第三道：模拟复现校验——在隔离的代码执行沙箱中尝试复现工具调用
          序列的关键步骤，验证其是否仍存在已知的失败模式。仅当三道校验全部通过时，该失败
          经验方可获得晋升资格，其警示标签从“CAUTION”降级为“NORMAL”；任一校验不通过，
          经验将被锁定在当前层级并标记为“永久警示”，仅可人工干预解除。不参与晋升决策或
          数据修改，仅执行安全仲裁判定。

依赖模块:
    ag-mem-24(L3中期层存储单元), ag-mem-45(安全规则库),
    ag-ecc-07(代码执行沙箱), ag-mem-35(三维权重系数配置单元)
被依赖模块:
    ag-mem-24, ag-mem-39(层级单向搬运写入单元), ag-mem-51(记忆变更日志追溯单元)

安全约束:
  R-01: 任何一道校验不通过，该失败经验必须被永久锁定在 L3 层，不得晋升至 L4 或 L5
  R-02: 永久警示标记仅可通过人工干预解除，不得被任何自动化模块修改
  R-03: 模拟复现校验必须在与主系统完全隔离的沙箱环境中执行，防止失败策略影响在线服务
  R-04: 安全仲裁的每一步结果都必须完整记录日志，不可篡改，供事后责任追溯
  R-05: 仲裁请求在系统繁忙时排队处理，但单条目总仲裁时间不得超过配置的最大超时

版本: V1.0 (Topic 修复版)
"""

import time
import uuid
from typing import Any, Dict, List, Optional
from enum import Enum

from memory_bus import InternalBus, Message


class ArbitrationState(Enum):
    IDLE = "idle"
    FETCHING_DATA = "fetching_data"
    CHECK_ONE = "check_one"
    CHECK_TWO = "check_two"
    CHECK_THREE = "check_three"
    ARBITRATION_DONE = "arbitration_done"
    SYSTEM_PAUSED = "system_paused"


class FailureArbitrationUnit:
    module_id = "ag-mem-43"
    module_name = "失败经验安全仲裁三道校验单元"
    version = "V1.0"

    CHECK_ONE_TIMEOUT_SEC = 10
    CHECK_TWO_TIMEOUT_SEC = 5
    CHECK_THREE_TIMEOUT_SEC = 30
    TOTAL_ARBITRATION_TIMEOUT_SEC = 60
    STATUS_REPORT_INTERVAL_SEC = 300
    MAX_QUEUE_SIZE = 50
    ALLOW_ARBITRATION_LAYER = "L3"

    def __init__(self):
        self.bus: Optional[InternalBus] = None
        self.state = ArbitrationState.IDLE
        self._queue: List[Dict[str, Any]] = []
        self._current_request: Optional[Dict[str, Any]] = None
        self._pending_data_fetch: Dict[str, Dict[str, Any]] = {}
        self._check_one_result: Optional[Dict[str, Any]] = None
        self._check_two_result: Optional[Dict[str, Any]] = None
        self._arbitration_start_time: float = 0.0
        self._step_start_time: float = 0.0
        self._total_arbitrated: int = 0
        self._passed_count: int = 0
        self._total_duration: float = 0.0
        self._last_status_time: float = time.time()
        self._pending_logs: List[Dict[str, Any]] = []

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    # ====================== 主循环 ======================
    def run_cycle(self):
        self.failure_arbitration_main_loop()

    def failure_arbitration_main_loop(self):
        if self.state == ArbitrationState.SYSTEM_PAUSED:
            return

        if self.bus:
            self.bus.process_batch(10)

        now = time.time()

        if now - self._last_status_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._report_status()
            self._last_status_time = now

        if self._current_request and (now - self._arbitration_start_time) > self.TOTAL_ARBITRATION_TIMEOUT_SEC:
            self._finalize_arbitration(passed=False, reason="总仲裁超时")
            return

        self._check_single_step_timeout(now)

        if self.state == ArbitrationState.IDLE:
            if self._queue:
                raw_request = self._queue.pop(0)
                self._start_arbitration(raw_request)

    def _check_single_step_timeout(self, now: float):
        if not self._current_request:
            return
        step_cost = now - self._step_start_time
        if self.state == ArbitrationState.CHECK_ONE and step_cost > self.CHECK_ONE_TIMEOUT_SEC:
            self._finalize_arbitration(passed=False, reason="第一道安全规则校验超时")
        if self.state == ArbitrationState.CHECK_THREE and step_cost > self.CHECK_THREE_TIMEOUT_SEC:
            self._finalize_arbitration(passed=False, reason="第三道沙箱复现校验超时")

    # ====================== 总线消息入口 ======================
    def handle_message(self, msg: Message):
        if not isinstance(msg.data, dict):
            self._log_event("INVALID_MSG_FORMAT", {"reason": "消息数据非字典格式"})
            return

        try:
            # 【修复】对齐上游 topic：仲裁请求
            if msg.topic == "ag-mem-43.arbitration_request":
                entry_id = msg.data.get("entry_id", "")
                # 如果已包含完整数据，直接入队；否则先拉取 L3 条目详情
                if msg.data.get("tool_call_sequence") and msg.data.get("task_description"):
                    if len(self._queue) < self.MAX_QUEUE_SIZE:
                        self._queue.append(msg.data)
                else:
                    self._pending_data_fetch[entry_id] = msg.data
                    if self.bus:
                        self.bus.publish_to_module(
                            target_module="ag-mem-24",
                            event_type="entry_query",
                            source_module=self.module_id,
                            data={"entry_id": entry_id}
                        )
                return

            # 来自 ag-mem-24 的条目详情回执
            if msg.topic == "ag-mem-43.entry_detail":
                entry_id = msg.data.get("entry_id", "")
                pending = self._pending_data_fetch.pop(entry_id, None)
                if pending:
                    full_request = {
                        **pending,
                        "tool_call_sequence": msg.data.get("tool_call_sequence", []),
                        "task_description": msg.data.get("task_description", ""),
                        "experience_data": msg.data.get("experience_data", {}),
                        "source_layer": pending.get("source_layer", self.ALLOW_ARBITRATION_LAYER)
                    }
                    if len(self._queue) < self.MAX_QUEUE_SIZE:
                        self._queue.append(full_request)
                return

            # 安全合规校验回执
            if msg.topic == "ag-mem-43.safety_check_response":
                self._handle_safety_check_response(msg.data)
                return

            # 模拟复现校验回执
            if msg.topic == "ag-mem-43.sandbox_response":
                self._handle_sandbox_response(msg.data)
                return
        except Exception as e:
            self._log_event("MSG_PROCESS_EXCEPTION", {"error_info": str(e)})

    # ====================== 仲裁流程 ======================
    def _start_arbitration(self, request_data: Dict[str, Any]):
        self._current_request = request_data
        self.state = ArbitrationState.CHECK_ONE
        self._arbitration_start_time = time.time()
        self._step_start_time = time.time()
        self._check_one_result = None
        self._check_two_result = None

        entry_layer = self._current_request.get("source_layer", "")
        if entry_layer != self.ALLOW_ARBITRATION_LAYER:
            self._finalize_arbitration(passed=False, reason=f"仅L3层级支持仲裁，当前层级：{entry_layer}")
            return

        if self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-45",
                event_type="safety_check",
                source_module=self.module_id,
                data={
                    "entry_id": self._current_request.get("entry_id"),
                    "tool_call_sequence": self._current_request.get("tool_call_sequence", []),
                    "operation_params": self._current_request.get("experience_data", {}).get("operation_params", {})
                }
            )

    def _handle_safety_check_response(self, data: Dict[str, Any]):
        if self.state != ArbitrationState.CHECK_ONE or not self._current_request:
            return

        compliant = data.get("compliant", False)
        violated_rules = data.get("violated_rules", [])
        self._check_one_result = {"compliant": compliant, "violated_rules": violated_rules}

        if not compliant:
            self._finalize_arbitration(passed=False, reason=f"安全规则不合规: {violated_rules}")
            return

        self.state = ArbitrationState.CHECK_TWO
        self._step_start_time = time.time()
        req = self._current_request
        consistent, conflict = self._perform_logic_check(
            req.get("tool_call_sequence", []),
            req.get("task_description", "")
        )
        self._check_two_result = {"consistent": consistent, "conflict": conflict}

        if not consistent:
            self._finalize_arbitration(passed=False, reason=f"逻辑不一致: {conflict}")
            return

        self.state = ArbitrationState.CHECK_THREE
        self._step_start_time = time.time()
        if self.bus:
            self.bus.publish_to_module(
                target_module="ag-ecc-07",
                event_type="sandbox_reproduce",
                source_module=self.module_id,
                data={
                    "entry_id": req.get("entry_id"),
                    "tool_call_sequence": req.get("tool_call_sequence", [])
                }
            )

    def _handle_sandbox_response(self, data: Dict[str, Any]):
        if self.state != ArbitrationState.CHECK_THREE or not self._current_request:
            return

        reproduced = data.get("reproduced_failure", False)
        failure_desc = data.get("failure_description", "")

        if reproduced:
            self._finalize_arbitration(passed=False, reason=f"模拟复现仍失败: {failure_desc}")
        else:
            self._finalize_arbitration(passed=True, reason="三道校验全部通过")

    def _perform_logic_check(self, tools: List[str], task_desc: str) -> tuple:
        task_lower = task_desc.lower()
        for tool in tools:
            tool_lower = tool.lower()
            if ("查询" in task_lower or "搜索" in task_lower or "获取" in task_lower) and \
               any(kw in tool_lower for kw in ["delete", "remove", "write", "modify"]):
                return False, f"任务目标({task_desc})与工具调用({tool})不匹配"
        return True, ""

    def _finalize_arbitration(self, passed: bool, reason: str):
        req = self._current_request
        if not req:
            return

        entry_id = req.get("entry_id", "")
        new_label = "NORMAL" if passed else "PERMANENT_CAUTION"

        if self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-24",
                event_type="arbitration_result",
                source_module=self.module_id,
                data={
                    "entry_id": entry_id,
                    "passed": passed,
                    "new_caution_label": new_label,
                    "reason": reason,
                    "check_one": self._check_one_result,
                    "check_two": self._check_two_result
                }
            )
            if not passed:
                self.bus.publish_to_module(
                    target_module="ag-mem-39",
                    event_type="lock_entry_promotion",
                    source_module=self.module_id,
                    data={
                        "entry_id": entry_id,
                        "locked": True,
                        "lock_reason": "仲裁不通过，永久锁定L3层级"
                    }
                )

        self._log_event("ARBITRATION_COMPLETED", {
            "entry_id": entry_id,
            "final_result": "通过" if passed else "拒绝",
            "new_caution_label": new_label,
            "total_reason": reason
        })

        self._total_arbitrated += 1
        if passed:
            self._passed_count += 1
        self._total_duration += (time.time() - self._arbitration_start_time) * 1000

        self._current_request = None
        self._check_one_result = None
        self._check_two_result = None
        self.state = ArbitrationState.IDLE

    # ====================== 状态上报 ======================
    def _report_status(self):
        if self.bus:
            pass_rate = self._passed_count / max(self._total_arbitrated, 1)
            self.bus.publish_to_module(
                target_module="ag-mem-03",
                event_type="internal_status",
                source_module=self.module_id,
                data={
                    "state": self.state.value,
                    "total_arbitrated": self._total_arbitrated,
                    "pass_rate": round(pass_rate, 3)
                }
            )

    # ====================== 管理接口 ======================
    def emergency_shutdown(self):
        self.state = ArbitrationState.SYSTEM_PAUSED
        self._queue.clear()
        self._pending_data_fetch.clear()
        self._current_request = None
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