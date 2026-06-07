#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-33
模块名称: 复用频次C值统计单元
所属分区: 三、漏斗二：任务经验漏斗 / 三维重要度计算引擎
核心职责: 统计各经验条目在同类任务场景下被成功调用并执行的次数，归一化为C值（0.0–1.0）。
          被频繁成功复用的经验获得高C值，驱动经验从"偶发行为"进化为"稳定技能"。
          不参与认知决策，仅执行复用频次的客观统计与归一化。

依赖模块:
    ag-mem-23(L2热度统计), ag-mem-15~19(场景分槽),
    ag-mem-36(I值聚合), ag-mem-35(权重配置), ag-mem-37(定时刷新)
被依赖模块:
    ag-mem-36, ag-mem-35, ag-mem-37

安全约束:
  C-01: C值计算仅基于调用频次与时间衰减，不访问经验内容
  C-02: L5核心层条目C值固定为1.0
  C-03: 异常波动告警（>0.5）仅为保护机制，不作为自动化决策依据
  C-04: 复用事件去重窗口300秒，不可设为0

版本: V1.0
"""

import time
import uuid
import math
from typing import Any, Dict, List, Optional
from enum import Enum

from memory_bus import InternalBus, Message


class StatisticsState(Enum):
    IDLE = "idle"
    INCREMENTAL_UPDATING = "incremental_updating"
    FULL_REFRESHING = "full_refreshing"
    SYSTEM_PAUSED = "system_paused"


class CValueStatistics:
    module_id = "ag-mem-33"
    module_name = "复用频次C值统计单元"
    version = "V1.0"

    DEFAULT_SATURATION_THRESHOLD = 10
    TIME_DECAY_LAMBDA = 0.01
    L4_TIME_DECAY_LAMBDA = 0.005
    DEDUP_WINDOW_SEC = 300
    ANOMALY_FLUCTUATION_THRESHOLD = 0.5

    SLOT_SATURATION_THRESHOLD = {
        "ag-mem-15": 8,
        "ag-mem-16": 10,
        "ag-mem-17": 12,
        "ag-mem-18": 10,
        "ag-mem-19": 15,
    }

    STATUS_REPORT_INTERVAL_SEC = 120

    def __init__(self):
        self.bus: Optional[InternalBus] = None
        self.state = StatisticsState.IDLE
        self._c_store: Dict[str, Dict[str, Any]] = {}
        self._last_refresh_time: float = 0.0
        self._last_status_time: float = time.time()
        self._pending_logs: List[Dict[str, Any]] = []

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成, "
              f"去重窗口={self.DEDUP_WINDOW_SEC}s")

    # ====================== 统一主循环入口 ======================
    def run_cycle(self):
        self.c_value_statistics_main_loop()

    def c_value_statistics_main_loop(self):
        if self.state == StatisticsState.SYSTEM_PAUSED:
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

        if msg.topic == "ag-mem-33.reuse_event":
            self._handle_reuse_event(msg)
            return

        if msg.topic == "ag-mem-33.full_refresh":
            self._handle_full_refresh(msg)
            return

        if msg.topic == "ag-mem-33.c_value_query":
            self._handle_c_value_query(msg)
            return

    def _handle_reuse_event(self, msg: Message):
        """增量更新：处理复用事件"""
        data = msg.data
        entry_id = data.get("entry_id", "")
        call_result = data.get("call_result", "成功")
        source_slot_id = data.get("source_slot_id", "")
        current_layer = data.get("current_layer", "L1")

        if not entry_id:
            return

        self.state = StatisticsState.INCREMENTAL_UPDATING
        now = time.time()

        if entry_id not in self._c_store:
            saturation = self.SLOT_SATURATION_THRESHOLD.get(
                source_slot_id, self.DEFAULT_SATURATION_THRESHOLD
            )
            self._c_store[entry_id] = self._new_record(entry_id, source_slot_id, current_layer, saturation)

        record = self._c_store[entry_id]
        record["current_layer"] = current_layer

        if call_result == "成功":
            if record["last_event_time"] > 0 and (now - record["last_event_time"]) < self.DEDUP_WINDOW_SEC:
                self.state = StatisticsState.IDLE
                return
            record["last_event_time"] = now

        previous_c = record["current_c_value"]
        if call_result == "成功":
            record["success_call_count"] += 1
            record["last_success_time"] = now
        elif record["last_success_time"] == 0.0:
            record["last_success_time"] = now

        new_c = self._calculate_c_value(record)
        record["current_c_value"] = new_c

        if abs(new_c - previous_c) > self.ANOMALY_FLUCTUATION_THRESHOLD and previous_c > 0:
            if self.bus:
                self.bus.publish_to_module("ag-mem-03", "anomaly_alert", self.module_id, {
                    "entry_id": entry_id,
                    "anomaly_type": "C值异常波动",
                    "current_c_value": new_c,
                    "previous_c_value": previous_c
                })

        if self.bus:
            self.bus.publish(
                topic=f"{msg.source_module}.c_value_update",
                source_module=self.module_id,
                data={"entry_id": entry_id, "c_value": new_c},
                target_module=msg.source_module,
                correlation_id=msg.correlation_id
            )

        self.state = StatisticsState.IDLE

    def _handle_full_refresh(self, msg: Message):
        """全量刷新：遍历所有条目重新计算C值，支持L2热度修正"""
        entries = msg.data.get("entries", [])
        if not entries:
            return

        l2_hits = msg.data.get("l2_hits", {})
        self.state = StatisticsState.FULL_REFRESHING
        start_time = time.time()
        now = time.time()
        updated = 0
        new_c_values = {}

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            eid = entry.get("entry_id", "")
            layer = entry.get("current_layer", "L1")
            slot = entry.get("source_slot_id", "ag-mem-19")
            success_count = entry.get("cumulative_success_count", 0)
            last_success_ts = entry.get("last_success_time", now)

            if layer == "L5":
                if eid not in self._c_store:
                    self._c_store[eid] = self._new_record(eid, slot, layer, 10)
                self._c_store[eid]["current_c_value"] = 1.0
                self._c_store[eid]["success_call_count"] = success_count
                new_c_values[eid] = 1.0
                updated += 1
                continue

            if eid not in self._c_store:
                saturation = self.SLOT_SATURATION_THRESHOLD.get(slot, self.DEFAULT_SATURATION_THRESHOLD)
                self._c_store[eid] = self._new_record(eid, slot, layer, saturation)
            
            rec = self._c_store[eid]
            rec["current_layer"] = layer
            rec["success_call_count"] = success_count
            rec["last_success_time"] = last_success_ts

            if layer == "L2" and eid in l2_hits:
                hit_count = l2_hits[eid].get("recent_7d_hits", 0)
                rec["success_call_count"] = max(rec["success_call_count"], hit_count // 2)

            previous_c = rec["current_c_value"]
            new_c = self._calculate_c_value(rec)
            rec["current_c_value"] = new_c

            if abs(new_c - previous_c) > self.ANOMALY_FLUCTUATION_THRESHOLD and previous_c > 0:
                if self.bus:
                    self.bus.publish_to_module("ag-mem-03", "anomaly_alert", self.module_id, {
                        "entry_id": eid,
                        "anomaly_type": "C值异常波动",
                        "current_c_value": new_c,
                        "previous_c_value": previous_c
                    })

            new_c_values[eid] = new_c
            updated += 1

        self._last_refresh_time = now
        elapsed = (time.time() - start_time) * 1000

        if self.bus:
            self.bus.publish(
                topic=f"{msg.source_module}.refresh_complete",
                source_module=self.module_id,
                data={
                    "updated_count": updated,
                    "new_c_values": new_c_values,
                    "update_duration_ms": elapsed
                },
                target_module=msg.source_module,
                correlation_id=msg.correlation_id
            )

        self.state = StatisticsState.IDLE

    def _handle_c_value_query(self, msg: Message):
        """查询单个条目的当前C值"""
        eid = msg.data.get("entry_id", "")
        c_val = 0.0
        if eid in self._c_store:
            c_val = self._c_store[eid].get("current_c_value", 0.0)
        
        if self.bus:
            self.bus.publish(
                topic=f"{msg.source_module}.c_value_response",
                source_module=self.module_id,
                data={"entry_id": eid, "c_value": round(c_val, 2)},
                target_module=msg.source_module,
                correlation_id=msg.correlation_id
            )

    # ====================== C值计算核心 ======================
    def _calculate_c_value(self, record: Dict[str, Any]) -> float:
        if record["current_layer"] == "L5":
            return 1.0

        now = time.time()
        last_success = record["last_success_time"]
        days_since = ((now - last_success) / 86400.0) if last_success > 0 else 999
        threshold = record["saturation_threshold"]
        call_norm = min(record["success_call_count"] / threshold, 1.0) if threshold > 0 else 0.0
        decay_lambda = self.L4_TIME_DECAY_LAMBDA if record["current_layer"] == "L4" else self.TIME_DECAY_LAMBDA
        decay = math.exp(-decay_lambda * days_since)
        c = call_norm * decay
        return round(min(max(c, 0.0), 1.0), 2)

    def _new_record(self, eid: str, slot: str, layer: str, sat: int) -> Dict[str, Any]:
        return {
            "entry_id": eid,
            "source_slot_id": slot,
            "current_layer": layer,
            "saturation_threshold": sat,
            "success_call_count": 0,
            "last_success_time": 0.0,
            "last_event_time": 0.0,
            "current_c_value": 0.0,
        }

    # ====================== 状态上报 ======================
    def _report_status(self):
        if not self._c_store or not self.bus:
            return
        total = len(self._c_store)
        avg_c = sum(v["current_c_value"] for v in self._c_store.values()) / total if total > 0 else 0.0
        
        self.bus.publish_to_module(
            target_module="ag-mem-03",
            event_type="internal_status",
            source_module=self.module_id,
            data={
                "state": self.state.value,
                "total_entries": total,
                "avg_c_value": round(avg_c, 3),
                "last_refresh_time": self._last_refresh_time
            }
        )

    # ====================== 管理接口 ======================
    def emergency_shutdown(self):
        self.state = StatisticsState.SYSTEM_PAUSED
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