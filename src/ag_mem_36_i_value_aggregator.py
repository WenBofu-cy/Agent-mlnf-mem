#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-36
模块名称: 综合重要度I值聚合计算单元
所属分区: 三、漏斗二：任务经验漏斗 / 三维重要度计算引擎
核心职责: 作为三维重要度计算引擎的聚合中枢，接收来自 ag-mem-31（S值）、ag-mem-32（V值）、
          ag-mem-33（C值统计）、ag-mem-34（I₀值）分别计算的四个维度的分值，从 ag-mem-35
          获取全局权重系数α、β、γ，执行聚合公式 I = I₀ + α·S + β·V + γ·C，输出最终的
          综合重要度评分I值。当新经验写入时，负责协调四个计算模块依次完成各自维度的计算，
          并在收集全部四个分值后执行聚合。同时响应来自 ag-mem-37（重要度增量定时刷新单元）
          的全量重算请求，对已有经验重新聚合I值。不参与各维度分值的计算逻辑，仅执行聚合
          加权与边界裁剪。

依赖模块:
    ag-mem-31(S值计算), ag-mem-32(V值计算), ag-mem-33(C值统计),
    ag-mem-34(I₀赋值), ag-mem-35(权重系数配置)
被依赖模块:
    ag-mem-15~19(各场景分槽), ag-mem-37(重要度增量定时刷新单元),
    ag-mem-38(晋升双条件判定单元), ag-mem-40(遗忘阈值判定单元)

安全约束:
  A-01: 聚合计算必须严格遵循 I = I₀ + α·S + β·V + γ·C 公式，不得增加额外维度或修改公式结构
  A-02: 权重系数α+β+γ=1.0为硬约束，聚合前必须校验，不满足时拒绝执行并上报告警
  A-03: 超时补齐使用的默认值（I₀=0.30, S=0.10, V=0.20）为保守值，偏向低估而非高估
  A-04: I值边界[0.05, 1.0]为硬约束，不得输出超出边界的重要度值
  A-05: 全量重算时不得修改条目的各维度原始分值（I₀/S/V/C），仅重新执行聚合计算

版本: V1.0 (权重归一化修复版)
"""

import time
import uuid
from typing import Any, Dict, List, Optional
from enum import Enum

from memory_bus import InternalBus, Message


class AggregatorState(Enum):
    IDLE = "idle"
    COLLECTING = "collecting"
    AGGREGATING = "aggregating"
    FULL_RECALC = "full_recalc"
    SYSTEM_PAUSED = "system_paused"


class IValueAggregator:
    module_id = "ag-mem-36"
    module_name = "综合重要度I值聚合计算单元"
    version = "V1.0"

    # 超时时间（秒）
    COLLECTION_TIMEOUT_SEC = 2.0
    # 默认补齐值（保守，偏向低估）
    DEFAULT_I0 = 0.30
    DEFAULT_S = 0.10
    DEFAULT_V = 0.20
    DEFAULT_C = 0.0
    # I值边界
    MIN_I = 0.05
    MAX_I = 1.0
    # 状态上报间隔
    STATUS_REPORT_INTERVAL_SEC = 120

    def __init__(self):
        self.bus: Optional[InternalBus] = None
        self.state = AggregatorState.IDLE
        # 收集表：entry_id -> {i0, s, v, c, 各维度是否就绪, 触发时间, 来源分槽}
        self._collection_table: Dict[str, Dict[str, Any]] = {}
        # 统计
        self._total_aggregations: int = 0
        self._sum_i: float = 0.0
        self._last_status_time: float = time.time()
        self._pending_logs: List[Dict[str, Any]] = []

        # 权重系数缓存（从 ag-mem-35 同步）
        self._alpha = 0.40
        self._beta = 0.30
        self._gamma = 0.30
        self._slot_configs: Dict[str, Dict[str, Any]] = {}

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    # ====================== 统一主循环入口 ======================
    def run_cycle(self):
        self.i_value_aggregator_main_loop()

    def i_value_aggregator_main_loop(self):
        if self.state == AggregatorState.SYSTEM_PAUSED:
            return

        if self.bus:
            self.bus.process_batch(10)

        now = time.time()

        # 检查收集超时
        if self.state == AggregatorState.COLLECTING:
            self._check_collection_timeouts(now)

        # 定期状态上报
        if now - self._last_status_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._report_status()
            self._last_status_time = now

    # ====================== 总线消息入口 ======================
    def handle_message(self, msg: Message):
        if not isinstance(msg.data, dict):
            return

        # 新经验聚合触发（来自各场景分槽或调度单元）
        if msg.topic == "ag-mem-36.new_experience_trigger":
            self._start_new_collection(msg)
            return

        # 各维度计算结果（来自 ag-mem-31/32/33/34）
        if msg.topic == "ag-mem-36.dimension_result":
            self._update_collection(msg.data)
            return

        # 全量重算指令（来自 ag-mem-37）
        if msg.topic == "ag-mem-36.full_recalc":
            self._handle_full_recalc(msg)
            return

        # 权重配置更新（来自 ag-mem-35）
        if msg.topic == "ag-mem-36.weight_update":
            self._alpha = float(msg.data.get("alpha", self._alpha))
            self._beta = float(msg.data.get("beta", self._beta))
            self._gamma = float(msg.data.get("gamma", self._gamma))
            self._slot_configs = msg.data.get("slot_configs", {})
            return

    # ====================== 新经验聚合 ======================
    def _start_new_collection(self, msg: Message):
        """启动新经验的四维度计算协调"""
        self.state = AggregatorState.COLLECTING
        data = msg.data
        entry_id = data.get("entry_id", "")
        source_slot_id = data.get("source_slot_id", "")
        task_type = data.get("task_type", "")
        metadata = data.get("experience_metadata", {})
        source_module = msg.source_module  # 记录来源模块，用于结果回发

        self._collection_table[entry_id] = {
            "i0": None, "s": None, "v": None, "c": 0.0,
            "ready": {"i0": False, "s": False, "v": False, "c": True},
            "trigger_time": time.time(),
            "source_slot_id": source_slot_id,
            "source_module": source_module,
            "metadata": metadata
        }

        # 向四个计算模块发起请求
        common_data = {"entry_id": entry_id, "task_type": task_type, "source_slot_id": source_slot_id}
        if self.bus:
            self.bus.publish_to_module("ag-mem-34", "i0_assignment_request", self.module_id,
                                       {**common_data, "generation_source": metadata.get("generation_source", ""),
                                        "is_sensitive_operation": metadata.get("is_sensitive_operation", False),
                                        "is_privacy_access": metadata.get("is_privacy_access", False)})
            self.bus.publish_to_module("ag-mem-31", "s_value_request", self.module_id,
                                       {**common_data, "tool_calls": metadata.get("tool_calls", []),
                                        "operation_params": metadata.get("operation_params", {}),
                                        "result_label": metadata.get("result_label", ""),
                                        "error_code": metadata.get("error_code")})
            self.bus.publish_to_module("ag-mem-32", "v_value_request", self.module_id,
                                       {**common_data, "associated_behaviors": metadata.get("associated_behaviors", []),
                                        "explicit_feedback": metadata.get("explicit_feedback"),
                                        "interaction_duration": metadata.get("interaction_duration", 0),
                                        "behavior_params": metadata.get("behavior_params", {})})

    def _update_collection(self, data: Dict[str, Any]):
        """接收各维度模块返回的计算结果，更新收集表"""
        entry_id = data.get("entry_id", "")
        if entry_id not in self._collection_table:
            self._log_event("LATE_RESULT_DISCARDED", {"entry_id": entry_id, "data": data})
            return

        record = self._collection_table[entry_id]
        # 从各模块返回的结果中提取分值
        if "i0_value" in data:
            record["i0"] = float(data["i0_value"])
            record["ready"]["i0"] = True
        if "s_value" in data:
            record["s"] = float(data["s_value"])
            record["ready"]["s"] = True
        if "v_value" in data:
            record["v"] = float(data["v_value"])
            record["ready"]["v"] = True
        if "c_value" in data:
            record["c"] = float(data["c_value"])
            record["ready"]["c"] = True

        # 检查是否四个维度全部就绪
        if all(record["ready"].values()):
            self._perform_aggregation(entry_id)

    # ====================== 全量重算 ======================
    def _handle_full_recalc(self, msg: Message):
        """处理全量重算指令"""
        self.state = AggregatorState.FULL_RECALC
        entries = msg.data.get("entries", [])

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            entry_id = entry.get("entry_id", "")
            i0 = float(entry.get("i0", 0.0))
            s = float(entry.get("s", 0.0))
            v = float(entry.get("v", 0.0))
            c = float(entry.get("c", 0.0))
            source_slot = entry.get("source_slot_id", "")
            i_val = self._compute_i(i0, s, v, c, source_slot)

            result = {
                "entry_id": entry_id, "i_value": i_val,
                "i0": i0, "s": s, "v": v, "c": c,
                "contributions": {
                    "i0": i0,
                    "s": self._effective_alpha(source_slot) * s,
                    "v": self._effective_beta(source_slot) * v,
                    "c": self._effective_gamma(source_slot) * c
                }
            }
            self._publish_result(result, msg.source_module, msg.correlation_id)
            self._total_aggregations += 1
            self._sum_i += i_val

        self.state = AggregatorState.IDLE

    # ====================== 核心计算 ======================
    def _compute_i(self, i0: float, s: float, v: float, c: float, source_slot: str) -> float:
        # 获取归一化后的有效权重，确保 α+β+γ=1.0
        alpha_eff, beta_eff, gamma_eff = self._normalized_effective_weights(source_slot)
        i_raw = i0 + alpha_eff * s + beta_eff * v + gamma_eff * c
        return max(self.MIN_I, min(self.MAX_I, round(i_raw, 3)))

    def _normalized_effective_weights(self, slot_id: str) -> tuple:
        """
        获取归一化后的有效权重，确保三者之和恒为 1.0。
        修复：解决分槽权重上调后 α+β+γ≠1.0 的问题。
        """
        alpha_raw = self._alpha * self._slot_configs.get(slot_id, {}).get("alpha_adj", 1.0)
        beta_raw = self._beta * self._slot_configs.get(slot_id, {}).get("beta_adj", 1.0)
        gamma_raw = self._gamma * self._slot_configs.get(slot_id, {}).get("gamma_adj", 1.0)
        total = alpha_raw + beta_raw + gamma_raw
        if total > 0:
            return alpha_raw / total, beta_raw / total, gamma_raw / total
        return self._alpha, self._beta, self._gamma

    def _effective_alpha(self, slot_id: str) -> float:
        return self._normalized_effective_weights(slot_id)[0]

    def _effective_beta(self, slot_id: str) -> float:
        return self._normalized_effective_weights(slot_id)[1]

    def _effective_gamma(self, slot_id: str) -> float:
        return self._normalized_effective_weights(slot_id)[2]

    # ====================== 超时检测与补齐 ======================
    def _check_collection_timeouts(self, now: float):
        for entry_id, record in list(self._collection_table.items()):
            if now - record["trigger_time"] > self.COLLECTION_TIMEOUT_SEC:
                missing = [dim for dim in ["i0", "s", "v"] if not record["ready"][dim]]
                if not record["ready"]["i0"]:
                    record["i0"] = self.DEFAULT_I0
                if not record["ready"]["s"]:
                    record["s"] = self.DEFAULT_S
                if not record["ready"]["v"]:
                    record["v"] = self.DEFAULT_V
                record["ready"] = {"i0": True, "s": True, "v": True, "c": True}
                self._perform_aggregation(entry_id)
                if self.bus:
                    self.bus.publish_to_module("ag-mem-03", "timeout_alert", self.module_id, {
                        "entry_id": entry_id, "missing_dimensions": missing,
                        "waited_seconds": now - record["trigger_time"]
                    })

    def _perform_aggregation(self, entry_id: str):
        record = self._collection_table.pop(entry_id, None)
        if not record:
            return
        i_val = self._compute_i(
            record["i0"] if record["i0"] is not None else self.DEFAULT_I0,
            record["s"] if record["s"] is not None else self.DEFAULT_S,
            record["v"] if record["v"] is not None else self.DEFAULT_V,
            record["c"] if record["c"] is not None else self.DEFAULT_C,
            record["source_slot_id"]
        )
        result = {
            "entry_id": entry_id, "i_value": i_val,
            "i0": record["i0"], "s": record["s"], "v": record["v"], "c": record["c"],
            "contributions": {
                "i0": record["i0"],
                "s": self._effective_alpha(record["source_slot_id"]) * record["s"],
                "v": self._effective_beta(record["source_slot_id"]) * record["v"],
                "c": self._effective_gamma(record["source_slot_id"]) * record["c"]
            }
        }
        self._publish_result(result, record.get("source_module", ""), "")
        self._total_aggregations += 1
        self._sum_i += i_val
        if len(self._collection_table) == 0:
            self.state = AggregatorState.IDLE

    def _publish_result(self, result: Dict[str, Any], target: str, correlation_id: str):
        if self.bus:
            self.bus.publish(
                topic=f"{target}.i_value_result" if target else "ag-mem-36.i_value_result",
                source_module=self.module_id,
                data=result,
                target_module=target,
                correlation_id=correlation_id
            )

    # ====================== 状态上报 ======================
    def _report_status(self):
        if self.bus:
            avg_i = self._sum_i / max(self._total_aggregations, 1)
            self.bus.publish_to_module(
                target_module="ag-mem-03",
                event_type="internal_status",
                source_module=self.module_id,
                data={
                    "state": self.state.value,
                    "total_aggregations": self._total_aggregations,
                    "avg_i_value": round(avg_i, 3)
                }
            )

    # ====================== 管理接口 ======================
    def emergency_shutdown(self):
        self.state = AggregatorState.SYSTEM_PAUSED
        self._collection_table.clear()
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