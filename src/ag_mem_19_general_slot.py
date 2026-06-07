#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-19
模块名称: 通用任务槽
所属分区: 三、漏斗二：任务经验漏斗 / 场景分槽管理
核心职责: 作为漏斗二中承载“通用任务”及混合/未分类任务经验的场景分槽。接收 ag-mem-03
          路由的通用任务场景经验条目，以及当其他四个场景分槽无法明确判定时作为默认兜底槽
          接收经验。管理该场景下的五层记忆存储（L1-L5）。本槽采用标准权重配置（无偏向上调），
          但拥有最强的遗忘保护参数，确保跨场景通用策略和不常见但可能有价值的混合经验不被
          过早清理。不参与认知决策，仅执行通用任务类经验的存储、检索与生命周期管理。

依赖模块:
    ag-mem-03(漏斗二专属调度单元), ag-mem-20~30(五层存储单元),
    ag-mem-35(三维权重系数配置单元)
被依赖模块:
    ag-mem-03, ag-mem-25(相似经验归并单元)

安全约束:
  S-01: 本槽接受“通用任务”标签经验及兜底路由的未分类经验，但不得主动接收明确属于其他
        四个场景的经验
  S-02: 兜底接收的经验在写入时不做任何权重调整，保持原始重要度值
  S-03: 强遗忘保护仅在本槽内生效，不得影响其他分槽的遗忘策略
  S-04: 槽内经验的跨槽查询必须通过 ag-mem-03 统一路由，不得直接响应外部模块的查询
  S-05: 兜底接收计数器仅用于统计，不得作为经验质量判断依据

版本: V1.0
"""

import time
import uuid
from typing import Any, Dict, List, Optional
from enum import Enum

from memory_bus import InternalBus, Message


class SlotState(Enum):
    IDLE = "idle"
    WRITING = "writing"
    QUERYING = "querying"
    MAINTENANCE = "maintenance"
    SYSTEM_PAUSED = "system_paused"


class GeneralSlot:
    module_id = "ag-mem-19"
    module_name = "通用任务槽"
    version = "V1.0"

    PROMOTION_THRESHOLDS = {
        "L1_to_L2": 0.42,
        "L2_to_L3": 0.62,
        "L3_to_L4": 0.82,
        "L4_to_L5": 0.92,
    }

    FORGET_THRESHOLDS = {
        "L1": 0.06,
        "L2": 0.15,
        "L3": 0.22,
        "L4": 0.18,
    }

    def __init__(self):
        self.bus: Optional[InternalBus] = None
        self.state = SlotState.IDLE

        self._entries: Dict[str, Dict[str, Dict[str, Any]]] = {
            "L1": {}, "L2": {}, "L3": {}, "L4": {}, "L5": {}
        }
        self._layer_counts = {"L1": 0, "L2": 0, "L3": 0, "L4": 0, "L5": 0}
        self._total_entries: int = 0
        self._fallback_count: int = 0

        self._last_status_time = time.time()
        self._pending_logs: List[Dict[str, Any]] = []

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成, "
              f"标准权重, 强遗忘保护已启用")

    # ====================== 统一主循环入口 ======================
    def run_cycle(self):
        self.general_slot_main_loop()

    def general_slot_main_loop(self):
        if self.state == SlotState.SYSTEM_PAUSED:
            return

        if self.bus:
            self.bus.process_batch(10)

        now = time.time()
        if now - self._last_status_time >= 60:
            self._report_status()
            self._last_status_time = now

    # ====================== 总线消息入口 ======================
    def handle_message(self, msg: Message):
        if not isinstance(msg.data, dict):
            return

        if msg.topic == "ag-mem-19.experience_write":
            self._handle_write(msg)
        elif msg.topic == "ag-mem-19.experience_query":
            self._handle_query(msg)
        elif msg.topic == "ag-mem-19.maintenance":
            self._handle_maintenance(msg)

    def _handle_write(self, msg: Message):
        data = msg.data
        scene_label = data.get("scene_label", "")
        is_fallback = data.get("is_fallback", False)

        if scene_label != "通用任务" and not is_fallback:
            self._reply_write_confirm(msg, "", "L1", 0, False, "场景标签不匹配且非兜底路由")
            self._log_event("WRITE_REJECTED", {"reason": "非通用任务且非兜底", "received": scene_label})
            return

        self.state = SlotState.WRITING
        start_time = time.time()

        experience_data = data.get("experience_data", {})
        i0_value = float(data.get("i0_value", 0.0))
        s_value = float(data.get("s_value", 0.0))
        v_value = float(data.get("v_value", 0.0))
        c_value = float(data.get("c_value", 0.0))
        reference_i_value = float(data.get("i_value", i0_value))

        if is_fallback:
            self._fallback_count += 1

        prefix = "FALLBACK" if is_fallback else "GENERAL"
        entry_id = f"L1-{prefix}-{uuid.uuid4().hex[:8]}"
        entry = {
            "entry_id": entry_id,
            "experience_data": experience_data,
            "i0_value": i0_value,
            "i_value": reference_i_value,
            "s_value": s_value,
            "v_value": v_value,
            "c_value": c_value,
            "is_fallback": is_fallback,
            "source_slot": self.module_id,
            "timestamp": time.time()
        }

        self._entries["L1"][entry_id] = entry
        self._layer_counts["L1"] += 1
        self._total_entries += 1

        elapsed_ms = (time.time() - start_time) * 1000
        self._reply_write_confirm(msg, entry_id, "L1", elapsed_ms, True, is_fallback=is_fallback)
        self._log_event("EXPERIENCE_WRITTEN", {
            "entry_id": entry_id,
            "layer": "L1",
            "is_fallback": is_fallback
        })
        self.state = SlotState.IDLE

    def _handle_query(self, msg: Message):
        self.state = SlotState.QUERYING
        start_time = time.time()

        conditions = msg.data.get("query", {})
        keywords = conditions.get("keywords", [])
        max_results = msg.data.get("max_results", 20)

        matched = []
        for layer in ["L5", "L4", "L3", "L2", "L1"]:
            for entry in self._entries[layer].values():
                if keywords:
                    text = str(entry.get("experience_data", ""))
                    if not any(kw in text for kw in keywords):
                        continue
                matched.append({**entry, "layer": layer})
                if len(matched) >= max_results:
                    break
            if len(matched) >= max_results:
                break

        matched.sort(key=lambda x: x.get("i_value", 0), reverse=True)
        matched = matched[:max_results]

        elapsed_ms = (time.time() - start_time) * 1000
        self._reply_query_result(msg, matched, elapsed_ms)
        self._log_event("QUERY_DONE", {"hit_count": len(matched), "keywords": keywords})
        self.state = SlotState.IDLE

    def _handle_maintenance(self, msg: Message):
        self.state = SlotState.MAINTENANCE
        self._log_event("MAINTENANCE_START", {"slot": self.module_id})

        forget_candidates = []
        promote_candidates = []

        for layer in ["L1", "L2", "L3", "L4"]:
            forget_threshold = self.FORGET_THRESHOLDS.get(layer, 0.10)
            if layer == "L1":
                promote_threshold = self.PROMOTION_THRESHOLDS["L1_to_L2"]
                target_layer = "L2"
            elif layer == "L2":
                promote_threshold = self.PROMOTION_THRESHOLDS["L2_to_L3"]
                target_layer = "L3"
            elif layer == "L3":
                promote_threshold = self.PROMOTION_THRESHOLDS["L3_to_L4"]
                target_layer = "L4"
            else:
                promote_threshold = self.PROMOTION_THRESHOLDS["L4_to_L5"]
                target_layer = "L5"

            for eid, entry in self._entries[layer].items():
                i_val = entry.get("i_value", entry.get("i0_value", 0))

                if i_val < forget_threshold:
                    forget_candidates.append({
                        "entry_id": eid,
                        "layer": layer,
                        "i_value": round(i_val, 2),
                        "reason": f"I值低于遗忘阈值{forget_threshold}（强保护）"
                    })
                elif i_val >= promote_threshold:
                    promote_candidates.append({
                        "entry_id": eid,
                        "layer": layer,
                        "target_layer": target_layer,
                        "i_value": round(i_val, 2),
                        "reason": f"I值满足晋升阈值{promote_threshold}"
                    })

        if self.bus and (forget_candidates or promote_candidates):
            self.bus.publish(
                topic="ag-mem-03.slot_result",
                source_module=self.module_id,
                data={
                    "action": "maintenance_result",
                    "slot_id": self.module_id,
                    "forget_candidates": forget_candidates,
                    "promote_candidates": promote_candidates
                },
                target_module="ag-mem-03"
            )

        self._log_event("MAINTENANCE_DONE", {
            "forget": len(forget_candidates),
            "promote": len(promote_candidates),
            "forget_thresholds": self.FORGET_THRESHOLDS
        })
        self.state = SlotState.IDLE

    # ====================== 回复工具 ======================
    def _reply_write_confirm(self, msg: Message, entry_id: str, layer: str,
                             duration_ms: float, success: bool, error: str = "",
                             is_fallback: bool = False):
        if not self.bus:
            return
        self.bus.publish(
            topic="ag-mem-03.slot_result",
            source_module=self.module_id,
            data={
                "success": success,
                "entry_id": entry_id,
                "assigned_layer": layer,
                "write_duration_ms": duration_ms,
                "error": error,
                "is_fallback": is_fallback
            },
            target_module="ag-mem-03",
            correlation_id=msg.correlation_id
        )

    def _reply_query_result(self, msg: Message, matched: List[Dict], duration_ms: float):
        if not self.bus:
            return
        self.bus.publish(
            topic="ag-mem-03.slot_result",
            source_module=self.module_id,
            data={
                "matched_experiences": matched,
                "total_count": len(matched),
                "query_duration_ms": duration_ms
            },
            target_module="ag-mem-03",
            correlation_id=msg.correlation_id
        )

    def _report_status(self):
        if self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-03",
                event_type="internal_status",
                source_module=self.module_id,
                data={
                    "total_entries": self._total_entries,
                    "layer_distribution": self._layer_counts.copy(),
                    "fallback_count": self._fallback_count
                }
            )

    # ====================== 管理接口 ======================
    def emergency_shutdown(self):
        self.state = SlotState.SYSTEM_PAUSED
        self._entries = {"L1": {}, "L2": {}, "L3": {}, "L4": {}, "L5": {}}
        self._layer_counts = {k: 0 for k in self._layer_counts}
        self._total_entries = 0
        self._fallback_count = 0
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