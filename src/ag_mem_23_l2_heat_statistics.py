#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-23
模块名称: L2近期层热度统计单元
所属分区: 三、漏斗二：任务经验漏斗 / 五层存储
核心职责: 统计L2近期层中各经验条目被查询命中的频率。被动接收 ag-mem-22 推送的命中事件，
          更新命中计数。为 ag-mem-33（C值统计）提供命中频次，为 ag-mem-38（晋升判定）
          提供热度参考。不参与晋升决策，仅执行命中事件的记录与统计。

依赖模块: ag-mem-22, ag-mem-33, ag-mem-38
被依赖模块: ag-mem-33, ag-mem-38

安全约束:
  S-01: 热度统计数据仅反映查询命中频率，不包含任何用户个人信息或经验内容
  S-02: 热度统计表仅存于内存，不持久化，系统重启后数据从零开始积累
  S-03: 本模块不主动查询L2存储内容，仅被动接收ag-mem-22推送的命中事件
  S-04: 热度数据的跨槽查询禁止：某个分槽查询时仅返回与该分槽相关的命中分布

版本: V1.0
"""

import time
import uuid
from typing import Any, Dict, List, Optional
from enum import Enum

from memory_bus import InternalBus, Message


class StatisticsState(Enum):
    IDLE = "idle"
    UPDATING = "updating"
    RESPONDING = "responding"
    SYSTEM_PAUSED = "system_paused"


class L2HeatStatistics:
    module_id = "ag-mem-23"
    module_name = "L2近期层热度统计单元"
    version = "V1.0"

    CLEANUP_INTERVAL_HOURS = 24
    STATUS_REPORT_INTERVAL_SEC = 60
    CLEANUP_STALE_DAYS = 7

    def __init__(self):
        self.bus: Optional[InternalBus] = None
        self.state = StatisticsState.IDLE
        self._heat_table: Dict[str, Dict[str, Any]] = {}
        self._today_hits: int = 0
        self._last_cleanup_time = time.time()
        self._last_status_time = time.time()
        self._pending_logs: List[Dict[str, Any]] = []

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    # ====================== 统一主循环入口 ======================
    def run_cycle(self):
        self.l2_heat_statistics_main_loop()

    def l2_heat_statistics_main_loop(self):
        if self.state == StatisticsState.SYSTEM_PAUSED:
            return

        if self.bus:
            self.bus.process_batch(10)

        now = time.time()

        if now - self._last_cleanup_time >= self.CLEANUP_INTERVAL_HOURS * 3600:
            self._cleanup_stale_entries(now)
            self._last_cleanup_time = now

        if now - self._last_status_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._report_status()
            self._last_status_time = now

    # ====================== 总线消息入口 ======================
    def handle_message(self, msg: Message):
        if not isinstance(msg.data, dict):
            return

        if msg.topic == "ag-mem-23.new_entries":
            entry_ids = msg.data.get("entry_ids", [])
            for eid in entry_ids:
                if eid not in self._heat_table:
                    self._heat_table[eid] = {
                        "entry_id": eid,
                        "total_hits": 0,
                        "recent_7d_hits": 0,
                        "recent_24h_hits": 0,
                        "last_hit_time": None,
                        "first_hit_time": None,
                        "hit_source_distribution": {}
                    }
            return

        if msg.topic == "ag-mem-23.hit_entries":
            hits = msg.data.get("hits", [])
            self._handle_hits(hits)
            return

        if msg.topic == "ag-mem-23.heat_query":
            self._handle_query(msg)
            return

    def _handle_hits(self, hits: List[Dict[str, Any]]):
        self.state = StatisticsState.UPDATING
        now = time.time()
        for hit in hits:
            eid = hit.get("entry_id")
            if not eid:
                continue
            if eid not in self._heat_table:
                self._heat_table[eid] = {
                    "entry_id": eid,
                    "total_hits": 0,
                    "recent_7d_hits": 0,
                    "recent_24h_hits": 0,
                    "last_hit_time": None,
                    "first_hit_time": now,
                    "hit_source_distribution": {}
                }
            data = self._heat_table[eid]
            data["total_hits"] += 1
            data["recent_7d_hits"] += 1
            data["recent_24h_hits"] += 1
            data["last_hit_time"] = now
            if data["first_hit_time"] is None:
                data["first_hit_time"] = now

            source = hit.get("query_source_slot", "")
            if source:
                dist = data["hit_source_distribution"]
                dist[source] = dist.get(source, 0) + 1

            self._today_hits += 1
        self.state = StatisticsState.IDLE

    def _handle_query(self, msg: Message):
        entry_ids = msg.data.get("entry_ids", [])
        self.state = StatisticsState.RESPONDING
        start_time = time.time()
        result_data = {}
        for eid in entry_ids:
            result_data[eid] = self._heat_table.get(eid, {
                "entry_id": eid,
                "total_hits": 0,
                "recent_7d_hits": 0,
                "recent_24h_hits": 0,
                "last_hit_time": None,
                "first_hit_time": None,
                "hit_source_distribution": {}
            })

        elapsed = (time.time() - start_time) * 1000
        if self.bus:
            self.bus.publish(
                topic=f"{msg.source_module}.heat_result",
                source_module=self.module_id,
                data={
                    "data": result_data,
                    "query_duration_ms": elapsed
                },
                target_module=msg.source_module,
                correlation_id=msg.correlation_id
            )
        self.state = StatisticsState.IDLE

    def _cleanup_stale_entries(self, now: float):
        stale_ids = []
        threshold = self.CLEANUP_STALE_DAYS * 24 * 3600
        for eid, data in self._heat_table.items():
            if data["total_hits"] == 0 and data["first_hit_time"] is not None:
                if now - data["first_hit_time"] > threshold:
                    stale_ids.append(eid)
        for eid in stale_ids:
            del self._heat_table[eid]
        if stale_ids:
            self._log_event("CLEANUP_STALE", {"removed_count": len(stale_ids)})

    def _report_status(self):
        if self.bus:
            total = len(self._heat_table)
            avg = self._today_hits / max(total, 1)
            self.bus.publish_to_module(
                target_module="ag-mem-03",
                event_type="internal_status",
                source_module=self.module_id,
                data={
                    "total_tracked_entries": total,
                    "today_total_hits": self._today_hits,
                    "avg_hit_rate": round(avg, 2)
                }
            )

    # ====================== 管理接口 ======================
    def emergency_shutdown(self):
        self.state = StatisticsState.SYSTEM_PAUSED
        self._heat_table.clear()
        self._today_hits = 0
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