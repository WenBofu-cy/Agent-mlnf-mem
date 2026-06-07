#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-26
模块名称: L4长期层存储单元
所属分区: 三、漏斗二：任务经验漏斗 / 五层存储
核心职责: 作为漏斗二五层记忆存储架构的第四层，专门存储从L3晋升而来的跨场景可泛化复用的
          高阶任务技能经验。对写入的经验数据进行去个性化处理，通过ag-mem-27提取通用规则。
          L4层条目默认受遗忘保护，仅当I值降至遗忘阈值以下且复用频次验证不足时才被遗忘。
          晋升至L5核心层的经验需通过更高门槛。不参与晋升判定或遗忘决策，仅执行经验的接收、
          存储、泛化管理与基础管理。

依赖模块:
    ag-mem-24(L3中期层存储单元), ag-mem-27(L4抽象提炼单元),
    ag-mem-28(L5核心层存储单元), ag-mem-40(遗忘阈值判定单元),
    ag-mem-48(全局容量配额管控单元)
被依赖模块:
    ag-mem-24, ag-mem-27, ag-mem-15~19(各场景分槽)

安全约束:
  S-01: 所有写入L4的经验必须经过去个性化处理，禁止保留任何可关联到特定用户的个人信息
  S-02: L4不接受任何警示标签（CAUTION/PERMANENT_CAUTION）的经验，确保长期层不包含失败策略
  S-03: L4层经验受强遗忘保护，遗忘阈值显著低于其他层级
  S-04: 去个性化后的重要度重算必须去除V值（用户价值）维度，仅基于安全显著性与复用频次
  S-05: L4层经验在晋升L5时必须额外通过安全底线校验（由ag-mem-43执行）

版本: V1.0
"""

import time
import uuid
from typing import Any, Dict, List, Optional
from enum import Enum

from memory_bus import InternalBus, Message


class StorageState(Enum):
    NORMAL = "normal"
    CAPACITY_WARNING = "capacity_warning"
    CAPACITY_CRITICAL = "capacity_critical"
    ABSTRACTING = "abstracting"
    SYSTEM_PAUSED = "system_paused"


class L4LongTermStorage:
    module_id = "ag-mem-26"
    module_name = "L4长期层存储单元"
    version = "V1.0"

    MAX_ENTRIES = 1000
    MAX_ENTRY_SIZE_BYTES = 25 * 1024
    CAPACITY_WARN_THRESHOLD = 0.80
    CAPACITY_CRITICAL_THRESHOLD = 0.95
    ABSTRACT_TRIGGER_COUNT = 20
    ABSTRACT_TIMED_INTERVAL_SEC = 72 * 3600
    FORGET_SCAN_INTERVAL_SEC = 24 * 3600
    STATUS_REPORT_INTERVAL_SEC = 120

    # 去个性化白名单：仅保留这些字段
    ANONYMIZE_KEEP_FIELDS = {
        "tool", "tool_sequence", "task_feature_vector", "task_vector",
        "result_label", "tags", "task_type", "scene_category",
        "tool_name", "api_name", "action_type", "parameters"
    }

    def __init__(self):
        self.bus: Optional[InternalBus] = None
        self.state = StorageState.NORMAL
        self._entries: Dict[str, Dict[str, Any]] = {}
        self._entry_count: int = 0
        self._abstract_counter: Dict[str, int] = {}
        self._recent_90d_writes: int = 0
        self._last_abstract_time: float = time.time()
        self._last_forget_scan: float = time.time()
        self._last_status_time: float = time.time()
        self._pending_logs: List[Dict[str, Any]] = []

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成, 最大条目={self.MAX_ENTRIES}")

    # ====================== 统一主循环入口 ======================
    def run_cycle(self):
        self.l4_storage_main_loop()

    def l4_storage_main_loop(self):
        if self.state == StorageState.SYSTEM_PAUSED:
            return

        if self.bus:
            self.bus.process_batch(10)

        now = time.time()

        # 定时遗忘扫描
        if now - self._last_forget_scan >= self.FORGET_SCAN_INTERVAL_SEC:
            self._trigger_forget("定时遗忘扫描")
            self._last_forget_scan = now

        # 定时抽象提炼
        if now - self._last_abstract_time >= self.ABSTRACT_TIMED_INTERVAL_SEC:
            self._trigger_timed_abstraction()
            self._last_abstract_time = now

        # 状态上报
        if now - self._last_status_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._report_status()
            self._last_status_time = now

    # ====================== 总线消息入口 ======================
    def handle_message(self, msg: Message):
        if not isinstance(msg.data, dict):
            return

        if msg.topic == "ag-mem-26.promoted_entries":
            self._handle_promotion(msg)
            return

        if msg.topic == "ag-mem-26.experience_query":
            self._handle_query(msg)
            return

        if msg.topic == "ag-mem-26.abstraction_complete":
            self._handle_abstraction_complete(msg.data)
            return

        if msg.topic == "ag-mem-26.cleanup_complete":
            self._handle_cleanup_complete(msg.data)
            return

    def _handle_promotion(self, msg: Message):
        entries = msg.data.get("entries", [])
        if not entries:
            return

        usage_pct = self._calculate_usage_pct()
        if usage_pct >= self.CAPACITY_CRITICAL_THRESHOLD:
            self.state = StorageState.CAPACITY_CRITICAL
            self._trigger_forget("容量紧急")
        elif usage_pct >= self.CAPACITY_WARN_THRESHOLD:
            self.state = StorageState.CAPACITY_WARNING

        if self.state == StorageState.CAPACITY_CRITICAL and self._entry_count >= self.MAX_ENTRIES:
            self._trigger_forget("容量紧急")
            if self._entry_count >= self.MAX_ENTRIES:
                self._reply_promotion_confirm(msg, len(entries), 0, 0)
                return

        received = len(entries)
        success_count = 0
        anonymized_count = 0

        for entry_data in entries:
            if not isinstance(entry_data, dict):
                continue

            # S-02: 拒绝 CAUTION 和 PERMANENT_CAUTION 条目
            caution_label = entry_data.get("caution_label", "NORMAL")
            if caution_label in ("CAUTION", "PERMANENT_CAUTION"):
                self._log_event("CAUTION_REJECTED_L4", {
                    "entry_id": entry_data.get("entry_id", ""),
                    "caution_label": caution_label
                })
                continue

            # S-01: 去个性化处理
            experience_data = self._anonymize_data(entry_data.get("experience_data", {}))
            anonymized_count += 1

            # S-04: 重算I值（去除V值）
            s_value = float(entry_data.get("s_value", 0))
            c_value = float(entry_data.get("c_value", 0))
            new_i = round(min(max(0.10 + 0.40 * s_value + 0.30 * c_value, 0.05), 1.0), 3)

            entry_id = entry_data.get("entry_id", f"L4-{uuid.uuid4().hex[:8]}")
            entry = {
                "entry_id": entry_id,
                "source_slot_id": entry_data.get("source_slot_id", ""),
                "experience_data": experience_data,
                "i_value": new_i,
                "s_value": s_value,
                "v_value": 0.0,
                "c_value": c_value,
                "result_label": entry_data.get("result_label", "成功"),
                "caution_label": "NORMAL",
                "abstracted": False,
                "promoted_at": time.time()
            }

            self._entries[entry_id] = entry
            self._entry_count += 1
            success_count += 1

            # 更新抽象计数器
            slot = entry["source_slot_id"]
            if slot not in self._abstract_counter:
                self._abstract_counter[slot] = 0
            self._abstract_counter[slot] += 1
            if self._abstract_counter[slot] >= self.ABSTRACT_TRIGGER_COUNT:
                self._trigger_abstraction_for_slot(slot)
                self._abstract_counter[slot] = 0

        self._recent_90d_writes += success_count
        self._reply_promotion_confirm(msg, received, success_count, anonymized_count)

    def _handle_query(self, msg: Message):
        source_slot_id = msg.data.get("source_slot_id", "")
        keywords = msg.data.get("query", {}).get("keywords", [])
        max_results = msg.data.get("max_results", 20)

        matched = []
        for entry in self._entries.values():
            if source_slot_id and entry.get("source_slot_id") != source_slot_id:
                continue
            if keywords:
                text = str(entry.get("experience_data", ""))
                if not any(kw in text for kw in keywords):
                    continue
            matched.append(entry)

        matched.sort(key=lambda x: x.get("i_value", 0), reverse=True)
        matched = matched[:max_results]

        if self.bus:
            self.bus.publish(
                topic=f"{msg.source_module}.query_response",
                source_module=self.module_id,
                data={"matched_experiences": matched, "total_count": len(matched), "layer": "L4"},
                target_module=msg.source_module,
                correlation_id=msg.correlation_id
            )

    def _handle_abstraction_complete(self, data: Dict[str, Any]):
        entry_ids = data.get("entry_ids", [])
        rule_id = data.get("rule_id", "")
        for eid in entry_ids:
            if eid in self._entries:
                self._entries[eid]["abstracted"] = True
                self._entries[eid]["related_rule_id"] = rule_id

    def _handle_cleanup_complete(self, data: Dict[str, Any]):
        cleared_ids = data.get("cleared_ids", [])
        for eid in cleared_ids:
            if eid in self._entries:
                del self._entries[eid]
        self._entry_count = len(self._entries)
        if self._calculate_usage_pct() < self.CAPACITY_WARN_THRESHOLD:
            self.state = StorageState.NORMAL

    def _anonymize_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        cleaned = {}
        for field in self.ANONYMIZE_KEEP_FIELDS:
            if field in data:
                cleaned[field] = data[field]
        cleaned["user_profile"] = "ANONYMOUS"
        return cleaned

    def _trigger_abstraction_for_slot(self, slot_id: str):
        unabstracted = [
            e for e in self._entries.values()
            if e["source_slot_id"] == slot_id and not e.get("abstracted", False)
        ]
        if len(unabstracted) >= 5 and self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-27",
                event_type="abstract_refine",
                source_module=self.module_id,
                data={"slot_id": slot_id, "entries": unabstracted, "reason": "累计触发"}
            )

    def _trigger_timed_abstraction(self):
        for slot_id in set(e["source_slot_id"] for e in self._entries.values()):
            self._trigger_abstraction_for_slot(slot_id)

    def _trigger_forget(self, reason: str):
        if self.bus and self._entries:
            self.bus.publish_to_module(
                target_module="ag-mem-40",
                event_type="forget_scan",
                source_module=self.module_id,
                data={"reason": reason, "entries": list(self._entries.values())}
            )

    def _reply_promotion_confirm(self, msg: Message, received: int, success: int, anonymized: int):
        if self.bus:
            self.bus.publish(
                topic=f"{msg.source_module}.write_confirm",
                source_module=self.module_id,
                data={
                    "received_count": received,
                    "success_count": success,
                    "anonymized_count": anonymized,
                    "l4_usage_pct": self._calculate_usage_pct()
                },
                target_module=msg.source_module,
                correlation_id=msg.correlation_id
            )

    def _calculate_usage_pct(self) -> float:
        return min(self._entry_count / self.MAX_ENTRIES, 1.0) if self.MAX_ENTRIES > 0 else 0.0

    def _report_status(self):
        if self.bus:
            abstracted = sum(1 for e in self._entries.values() if e.get("abstracted", False))
            self.bus.publish_to_module(
                target_module="ag-mem-48",
                event_type="storage_status",
                source_module=self.module_id,
                data={
                    "state": self.state.value,
                    "total_entries": self._entry_count,
                    "usage_pct": self._calculate_usage_pct(),
                    "abstracted_count": abstracted,
                    "recent_90d_writes": self._recent_90d_writes
                }
            )

    # ====================== 管理接口 ======================
    def emergency_shutdown(self):
        self.state = StorageState.SYSTEM_PAUSED
        # 安全规范：紧急停机清空内存数据
        self._entries.clear()
        self._abstract_counter.clear()
        self._entry_count = 0
        self._recent_90d_writes = 0
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