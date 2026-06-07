#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-15
模块名称: 对话交互槽
所属分区: 三、漏斗二：任务经验漏斗 / 场景分槽管理
核心职责: 作为漏斗二中专门承载“对话交互”类任务经验的场景分槽。接收 ag-mem-03 路由的
          对话交互场景经验条目，管理该场景下的五层记忆存储（L1-L5）。在本槽内，用户价值
          （V值）权重自动上调20%，以强化用户个性化对话策略的沉淀。同时管理该槽专属的晋升
          阈值与遗忘策略参数。不参与认知决策，仅执行对话交互类经验的存储、检索与生命周期
          管理。

依赖模块:
    ag-mem-03(漏斗二专属调度单元), ag-mem-20~30(五层存储单元),
    ag-mem-35(三维权重系数配置单元)
被依赖模块:
    ag-mem-03, ag-mem-25(相似经验归并单元)

安全约束:
  S-01: 本槽位仅接受场景标签确认为“对话交互”的经验条目，其他场景条目将被拒绝
  S-02: V值权重上调仅在本槽位内生效，不得影响其他分槽的重要度计算
  S-03: 槽内经验的跨槽查询必须通过 ag-mem-03 统一路由，不得直接响应外部模块的查询
  S-04: 维护扫描期间的遗忘操作必须遵循本槽专属的遗忘阈值，不得使用全局默认值

版本: V1.0 (最终修复版 · 全日志 · 熔断安全 · 规范对齐)
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


class DialogueSlot:
    module_id = "ag-mem-15"
    module_name = "对话交互槽"
    version = "V1.0"

    # 专属权重：V 值上调 20%
    V_WEIGHT_BOOST = 1.2

    # 专属晋升阈值（略低于标准，促进对话经验晋升）
    PROMOTION_THRESHOLDS = {
        "L1_to_L2": 0.35,
        "L2_to_L3": 0.55,
        "L3_to_L4": 0.75,
        "L4_to_L5": 0.90,
    }

    # 专属遗忘阈值（略低于标准，保留更多对话经验）
    FORGET_THRESHOLDS = {
        "L1": 0.08,
        "L2": 0.18,
        "L3": 0.28,
        "L4": 0.20,
    }

    def __init__(self):
        self.bus: Optional[InternalBus] = None
        self.state = SlotState.IDLE

        # 五层存储结构
        self._entries: Dict[str, Dict[str, Dict[str, Any]]] = {
            "L1": {}, "L2": {}, "L3": {}, "L4": {}, "L5": {}
        }
        self._layer_counts = {"L1": 0, "L2": 0, "L3": 0, "L4": 0, "L5": 0}
        self._total_entries: int = 0

        self._last_status_time = time.time()
        self._pending_logs: List[Dict[str, Any]] = []

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成, "
              f"V值权重上调20%, 专属晋升/遗忘阈值已加载")

    # ====================== 全系统统一主循环入口 ======================
    def run_cycle(self):
        self.dialogue_slot_main_loop()

    def dialogue_slot_main_loop(self):
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

        if msg.topic == "ag-mem-15.experience_write":
            self._handle_write(msg)
            return

        if msg.topic == "ag-mem-15.experience_query":
            self._handle_query(msg)
            return

        if msg.topic == "ag-mem-15.maintenance":
            self._handle_maintenance(msg)
            return

    def _handle_write(self, msg: Message):
        """处理经验写入请求"""
        data = msg.data
        scene_label = data.get("scene_label", "")

        # 校验场景标签 (S-01)
        if scene_label != "对话交互":
            self._reply_write_confirm(msg, "", "L1", 0, False, "场景标签不匹配")
            self._log_event("WRITE_REJECTED", {"reason": "场景不匹配", "received": scene_label})
            return

        self.state = SlotState.WRITING
        start_time = time.time()

        # 提取经验数据
        experience_data = data.get("experience_data", {})
        i0_value = float(data.get("i0_value", 0.0))
        s_value = float(data.get("s_value", 0.0))
        v_value = float(data.get("v_value", 0.0))
        c_value = float(data.get("c_value", 0.0))
        reference_i_value = float(data.get("i_value", 0.0))

        # 应用 V 值上调 (S-02)，仅调整 V 分量，不自行计算综合 I 值
        adjusted_v = min(v_value * self.V_WEIGHT_BOOST, 1.0)

        entry_id = f"L1-DIALOGUE-{uuid.uuid4().hex[:8]}"
        entry = {
            "entry_id": entry_id,
            "experience_data": experience_data,
            "i0_value": i0_value,
            "s_value": s_value,
            "v_value": adjusted_v,
            "c_value": c_value,
            "i_value": reference_i_value,
            "source_slot": self.module_id,
            "timestamp": time.time()
        }

        # 写入 L1 层
        self._entries["L1"][entry_id] = entry
        self._layer_counts["L1"] += 1
        self._total_entries += 1

        elapsed_ms = (time.time() - start_time) * 1000
        self._reply_write_confirm(msg, entry_id, "L1", elapsed_ms, True)
        self._log_event("EXPERIENCE_WRITTEN", {
            "entry_id": entry_id,
            "layer": "L1",
            "v_original": round(v_value,2),
            "v_adjusted": round(adjusted_v,2)
        })
        self.state = SlotState.IDLE

    def _handle_query(self, msg: Message):
        """处理经验查询请求"""
        self.state = SlotState.QUERYING
        start_time = time.time()

        conditions = msg.data.get("query", {})
        keywords = conditions.get("keywords", [])
        max_results = msg.data.get("max_results", 20)

        matched = []
        # 从 L5 到 L1 依次检索（核心层优先）
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
        """
        执行维护扫描（使用专属遗忘阈值）
        修正：不再直接删除条目，而是向 ag-mem-03 发送晋升/遗忘候选清单
        """
        self.state = SlotState.MAINTENANCE
        self._log_event("MAINTENANCE_START", {"slot": self.module_id})

        # 收集 L1/L2 层中低于遗忘阈值的条目
        forget_candidates = []
        promote_candidates = []

        for layer in ["L1", "L2"]:
            forget_threshold = self.FORGET_THRESHOLDS.get(layer, 0.10)
            # 确定晋升目标层级和阈值
            if layer == "L1":
                promote_threshold = self.PROMOTION_THRESHOLDS.get("L1_to_L2", 0.35)
                target_layer = "L2"
            else:
                promote_threshold = self.PROMOTION_THRESHOLDS.get("L2_to_L3", 0.55)
                target_layer = "L3"

            for eid, entry in self._entries[layer].items():
                i_val = entry.get("i_value", 0)
                if i_val < forget_threshold:
                    forget_candidates.append({
                        "entry_id": eid,
                        "layer": layer,
                        "i_value": round(i_val,2),
                        "reason": f"I值低于遗忘阈值{forget_threshold}"
                    })
                elif i_val >= promote_threshold:
                    promote_candidates.append({
                        "entry_id": eid,
                        "layer": layer,
                        "target_layer": target_layer,
                        "i_value": round(i_val,2),
                        "reason": f"I值满足晋升阈值{promote_threshold}"
                    })

        # 向 ag-mem-03 发送维护结果，由调度单元统一处理晋升与遗忘
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

    # ====================== 回复工具（统一回执给 ag-mem-03） ======================
    def _reply_write_confirm(self, msg: Message, entry_id: str, layer: str,
                             duration_ms: float, success: bool, error: str = ""):
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
                "error": error
            },
            target_module="ag-mem-03",
            correlation_id=msg.correlation_id
        )

    def _reply_query_result(self, msg: Message, matched: List[Dict],
                            duration_ms: float):
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
                    "layer_distribution": self._layer_counts.copy()
                }
            )

    # ====================== 管理接口 ======================
    def emergency_shutdown(self):
        self.state = SlotState.SYSTEM_PAUSED
        self._entries.clear()
        self._layer_counts = {k:0 for k in self._layer_counts}
        self._total_entries = 0
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