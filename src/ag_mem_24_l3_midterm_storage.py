#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-24
模块名称: L3中期层存储单元
所属分区: 三、漏斗二：任务经验漏斗 / 五层存储
核心职责: 作为漏斗二五层记忆存储架构的第三层，专门存储近30日内从L2晋升而来的稳定有效的
          任务策略经验。对失败经验强制标记CAUTION并锁定，禁止晋升至L4。当同一场景连续
          三次无警示安全通过后自动降级。管理L3层容量与条目生命周期，定期触发相似经验归并。

依赖模块: ag-mem-22, ag-mem-25, ag-mem-26, ag-mem-40, ag-mem-48
被依赖模块: ag-mem-22, ag-mem-25, ag-mem-15~19

安全约束:
  S-01: 失败经验必须强制标记CAUTION并锁定在L3层，禁止晋升至L4
  S-02: 警示条目查询时明确标注caution=true
  S-03: 警示标签降级必须严格满足"连续3次同场景安全通过"
  S-04: L3层条目留存超过30天后必须处理

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
    MAINTENANCE = "maintenance"
    SYSTEM_PAUSED = "system_paused"


class L3MidTermStorage:
    module_id = "ag-mem-24"
    module_name = "L3中期层存储单元"
    version = "V1.0"

    L3_CAPACITY_RATIO = 0.10
    MAX_ENTRIES = 2000
    MAX_ENTRY_SIZE_BYTES = 20 * 1024
    MAX_RETENTION_DAYS = 30
    CAPACITY_WARN_THRESHOLD = 0.80
    CAPACITY_CRITICAL_THRESHOLD = 0.95
    OVERDUE_CHECK_INTERVAL_SEC = 3600
    MERGE_TRIGGER_INTERVAL_SEC = 12 * 3600
    STATUS_REPORT_INTERVAL_SEC = 60
    CAUTION_DOWNGRADE_SAFE_COUNT = 3

    def __init__(self):
        self.bus: Optional[InternalBus] = None
        self.state = StorageState.NORMAL

        self._entries: Dict[str, Dict[str, Any]] = {}
        self._entry_count: int = 0
        self._caution_tracker: Dict[str, Dict[str, int]] = {}
        self._recent_30d_writes: int = 0
        self._last_overdue_check = time.time()
        self._last_merge_time = time.time()
        self._last_status_time = time.time()
        self._pending_logs: List[Dict[str, Any]] = []

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成, "
              f"最大条目={self.MAX_ENTRIES}, 最大留存={self.MAX_RETENTION_DAYS}天")

    # ====================== 统一主循环入口 ======================
    def run_cycle(self):
        self.l3_storage_main_loop()

    def l3_storage_main_loop(self):
        if self.state == StorageState.SYSTEM_PAUSED:
            return

        if self.bus:
            self.bus.process_batch(10)

        now = time.time()

        # 定时超期检查
        if now - self._last_overdue_check >= self.OVERDUE_CHECK_INTERVAL_SEC:
            self._handle_overdue_entries()
            self._last_overdue_check = now

        # 定时归并触发
        if now - self._last_merge_time >= self.MERGE_TRIGGER_INTERVAL_SEC:
            self._trigger_merge()
            self._last_merge_time = now

        # 定期状态上报
        if now - self._last_status_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._report_status()
            self._last_status_time = now

    # ====================== 总线消息入口 ======================
    def handle_message(self, msg: Message):
        if not isinstance(msg.data, dict):
            return

        # 接收来自 ag-mem-22 的晋升条目
        if msg.topic == "ag-mem-24.promoted_entries":
            self._handle_promotion(msg)
            return

        # 接收来自各分槽的查询请求
        if msg.topic == "ag-mem-24.experience_query":
            self._handle_query(msg)
            return

        # 接收场景安全通过通知
        if msg.topic == "ag-mem-24.scene_safety_pass":
            self._handle_safety_pass(msg.data)
            return

    def _handle_promotion(self, msg: Message):
        entries = msg.data.get("entries", [])
        if not entries:
            return

        # 容量检查
        usage_pct = self._calculate_usage_pct()
        if usage_pct >= self.CAPACITY_CRITICAL_THRESHOLD:
            self.state = StorageState.CAPACITY_CRITICAL
        elif usage_pct >= self.CAPACITY_WARN_THRESHOLD:
            self.state = StorageState.CAPACITY_WARNING

        received = len(entries)
        success_count = 0
        caution_marked = 0

        for entry_data in entries:
            if not isinstance(entry_data, dict):
                continue

            entry_size = len(str(entry_data.get("experience_data", {})))
            if entry_size > self.MAX_ENTRY_SIZE_BYTES:
                continue

            entry_id = entry_data.get("entry_id", f"L3-{uuid.uuid4().hex[:8]}")
            result_label = entry_data.get("result_label", "成功")
            caution_label = "NORMAL"

            # 失败经验标记CAUTION (S-01)
            if result_label in ("失败", "策略失误"):
                caution_label = "CAUTION"
                caution_marked += 1
                sig = self._generate_task_signature(entry_data)
                slot_id = entry_data.get("source_slot_id", "")
                if slot_id not in self._caution_tracker:
                    self._caution_tracker[slot_id] = {}
                self._caution_tracker[slot_id][sig] = 0
            else:
                sig = ""

            entry = {
                "entry_id": entry_id,
                "source_slot_id": entry_data.get("source_slot_id", ""),
                "experience_data": entry_data.get("experience_data", {}),
                "i_value": float(entry_data.get("i_value", 0)),
                "s_value": float(entry_data.get("s_value", 0)),
                "v_value": float(entry_data.get("v_value", 0)),
                "c_value": float(entry_data.get("c_value", 0)),
                "result_label": result_label,
                "caution_label": caution_label,
                "task_signature": sig,
                "promoted_at": time.time(),
                "last_accessed_at": time.time()
            }

            self._entries[entry_id] = entry
            self._entry_count += 1
            success_count += 1

        self._recent_30d_writes += success_count

        # 回复 ag-mem-22
        if self.bus:
            self.bus.publish(
                topic="ag-mem-22.promotion_confirm",
                source_module=self.module_id,
                data={
                    "received_count": received,
                    "success_count": success_count,
                    "caution_marked_count": caution_marked,
                    "l3_usage_pct": self._calculate_usage_pct()
                },
                target_module="ag-mem-22",
                correlation_id=msg.correlation_id
            )

    def _handle_query(self, msg: Message):
        source_slot = msg.data.get("source_slot", msg.source_module)
        conditions = msg.data.get("query", {})
        keywords = conditions.get("keywords", [])
        include_caution = msg.data.get("include_caution", False)
        max_results = msg.data.get("max_results", 20)

        matched = []
        for entry in self._entries.values():
            if source_slot and entry.get("source_slot_id") != source_slot:
                continue

            # 警示条目过滤 (S-02)
            if entry.get("caution_label") != "NORMAL" and not include_caution:
                continue

            if keywords:
                text = str(entry.get("experience_data", ""))
                if not any(kw in text for kw in keywords):
                    continue

            entry["last_accessed_at"] = time.time()
            matched.append(entry)

        matched.sort(key=lambda x: x.get("i_value", 0), reverse=True)
        matched = matched[:max_results]

        if self.bus:
            self.bus.publish(
                topic=f"{msg.source_module}.query_response",
                source_module=self.module_id,
                data={
                    "matched_experiences": matched,
                    "layer": "L3",
                    "total_count": len(matched)
                },
                target_module=msg.source_module,
                correlation_id=msg.correlation_id
            )

    def _handle_safety_pass(self, data: Dict[str, Any]):
        """处理场景安全通过通知 (S-03)"""
        slot_id = data.get("slot_id", "")
        sig = data.get("task_signature", "")
        tracker = self._caution_tracker.get(slot_id, {})
        if sig in tracker:
            tracker[sig] += 1
            if tracker[sig] >= self.CAUTION_DOWNGRADE_SAFE_COUNT:
                for entry in self._entries.values():
                    if (entry.get("caution_label") == "CAUTION" and
                        entry.get("source_slot_id") == slot_id and
                        entry.get("task_signature") == sig):
                        entry["caution_label"] = "NORMAL"
                        self._log_event("CAUTION_DOWNGRADED", {"entry_id": entry["entry_id"]})
                        break
                del tracker[sig]

    def _handle_overdue_entries(self):
        """超期条目处理 (S-04)，处理后从本地存储中移除"""
        now = time.time()
        to_remove = []
        for eid, entry in list(self._entries.items()):
            retention_days = (now - entry.get("promoted_at", now)) / 86400.0
            if retention_days > self.MAX_RETENTION_DAYS:
                if entry.get("caution_label") == "CAUTION":
                    self._send_to_forget([entry])
                elif entry.get("i_value", 0) >= 0.80:
                    self._send_to_l4([entry])
                else:
                    self._send_to_forget([entry])
                to_remove.append(eid)

                # 清理 caution tracker
                slot_id = entry.get("source_slot_id")
                sig = entry.get("task_signature")
                if slot_id in self._caution_tracker and sig in self._caution_tracker[slot_id]:
                    del self._caution_tracker[slot_id][sig]

        for eid in to_remove:
            del self._entries[eid]
            self._entry_count -= 1
        if to_remove:
            self._log_event("OVERDUE_PROCESSED", {"removed_count": len(to_remove)})

    def _send_to_l4(self, entries: List[Dict]):
        if self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-26",
                event_type="promoted_entries",
                source_module=self.module_id,
                data={"entries": entries}
            )

    def _send_to_forget(self, entries: List[Dict]):
        if self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-40",
                event_type="forget_candidates",
                source_module=self.module_id,
                data={"entries": entries}
            )

    def _trigger_merge(self):
        if self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-25",
                event_type="merge_scan",
                source_module=self.module_id,
                data={"entries": list(self._entries.values())}
            )

    def _generate_task_signature(self, entry_data: Dict) -> str:
        tools = str(entry_data.get("experience_data", {}).get("tools", ""))
        task_type = entry_data.get("experience_data", {}).get("task_type", "")
        slot_id = entry_data.get("source_slot_id", "")
        return f"{slot_id}:{task_type}:{hash(tools)}"

    def _calculate_usage_pct(self) -> float:
        if self.MAX_ENTRIES == 0:  # 修正：使用比较运算符 ==
            return 0.0
        return min(self._entry_count / self.MAX_ENTRIES, 1.0)

    def _report_status(self):
        if self.bus:
            caution_count = sum(1 for e in self._entries.values() if e.get("caution_label") != "NORMAL")
            self.bus.publish_to_module(
                target_module="ag-mem-48",
                event_type="storage_status",
                source_module=self.module_id,
                data={
                    "state": self.state.value,
                    "total_entries": self._entry_count,
                    "usage_pct": self._calculate_usage_pct(),
                    "caution_count": caution_count,
                    "recent_30d_writes": self._recent_30d_writes
                }
            )

    def get_entry_count(self) -> int:
        return self._entry_count

    # ====================== 管理接口 ======================
    def emergency_shutdown(self):
        self.state = StorageState.SYSTEM_PAUSED
        self._entries.clear()
        self._caution_tracker.clear()
        self._entry_count = 0
        self._recent_30d_writes = 0
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