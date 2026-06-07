#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-22
模块名称: L2近期层存储单元
所属分区: 三、漏斗二：任务经验漏斗 / 五层存储
核心职责: 作为漏斗二五层记忆存储架构的第二层，专门存储近7日内从L1晋升而来的频发场景经验。
          L2是经验从"临时暂存"进入"近期有效"的关键过渡层，占漏斗二总容量的25%。接收
          ag-mem-21（L1时序衰减单元）的晋升条目，管理L2层的容量与条目生命周期，为L3
          中期层提供晋升候选。不参与晋升判定或遗忘决策，仅执行经验的接收、存储与基础管理。

依赖模块:
    ag-mem-21(L1衰减评估单元), ag-mem-23(L2热度统计单元),
    ag-mem-24(L3中期层存储单元), ag-mem-48(全局容量配额管控单元)
被依赖模块:
    ag-mem-21, ag-mem-23, ag-mem-15~19(各场景分槽)

安全约束:
  S-01: L2层接收的晋升条目必须保留其原始来源分槽编号，用于分槽查询隔离
  S-02: L2层条目在留存超过7天后必须处理（晋升或清除），不得无限期滞留
  S-03: 分槽查询时，L2仅返回与请求来源分槽匹配的条目，不得跨槽返回其他分槽的经验
  S-04: L2存储的持久化写入必须保证原子性，写入中断时不产生损坏的半条记录

版本: V1.0 (已修复ag-mem-23数据链路)
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
    SYSTEM_PAUSED = "system_paused"


class L2RecentStorage:
    module_id = "ag-mem-22"
    module_name = "L2近期层存储单元"
    version = "V1.0"

    # 容量配置
    L2_CAPACITY_RATIO = 0.25
    MAX_ENTRIES = 5000
    MAX_ENTRY_SIZE_BYTES = 15 * 1024
    MAX_RETENTION_HOURS = 7 * 24
    CAPACITY_WARN_THRESHOLD = 0.80
    CAPACITY_CRITICAL_THRESHOLD = 0.95
    OVERDUE_CHECK_INTERVAL_SEC = 3600
    STATUS_REPORT_INTERVAL_SEC = 30

    def __init__(self):
        self.bus: Optional[InternalBus] = None
        self.state = StorageState.NORMAL

        self._entries: Dict[str, Dict[str, Any]] = {}
        self._entry_count: int = 0
        self._recent_7d_writes: int = 0
        self._recent_7d_queries: int = 0
        self._last_overdue_check = time.time()
        self._last_status_time = time.time()
        self._pending_logs: List[Dict[str, Any]] = []

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成, "
              f"最大条目={self.MAX_ENTRIES}, 最大留存={self.MAX_RETENTION_HOURS}h")

    # ====================== 主循环 ======================
    def l2_storage_main_loop(self):
        if self.state == StorageState.SYSTEM_PAUSED:
            return

        if self.bus:
            self.bus.process_batch(10)

        now = time.time()

        # 定时超期检查
        if now - self._last_overdue_check >= self.OVERDUE_CHECK_INTERVAL_SEC:
            self._handle_overdue_entries()
            self._last_overdue_check = now

        # 定期状态上报
        if now - self._last_status_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._report_status()
            self._last_status_time = now

    # ====================== 总线消息入口 ======================
    def handle_message(self, msg: Message):
        if not isinstance(msg.data, dict):
            return

        # 接收来自 ag-mem-21 的晋升条目
        if msg.topic == "ag-mem-22.promoted_entries":
            self._handle_promotion(msg)
            return

        # 接收来自各分槽的查询请求
        if msg.topic == "ag-mem-22.experience_query":
            self._handle_query(msg)
            return

    def _handle_promotion(self, msg: Message):
        """处理 L1 晋升条目"""
        entries = msg.data.get("entries", [])
        if not entries:
            return

        # 容量状态检查
        usage_pct = self._calculate_usage_pct()
        if usage_pct >= self.CAPACITY_CRITICAL_THRESHOLD:
            self.state = StorageState.CAPACITY_CRITICAL
        elif usage_pct >= self.CAPACITY_WARN_THRESHOLD:
            self.state = StorageState.CAPACITY_WARNING

        # 容量紧急处理
        if self.state == StorageState.CAPACITY_CRITICAL and self._entry_count >= self.MAX_ENTRIES:
            self._force_cleanup_overdue()
            if self._entry_count >= self.MAX_ENTRIES:
                self._force_cleanup_low_i()

        # 容量预警处理
        if self.state == StorageState.CAPACITY_WARNING:
            self._cleanup_expired_low_i()

        received = len(entries)
        success_count = 0
        new_entry_ids = []

        for entry_data in entries:
            if not isinstance(entry_data, dict):
                continue

            # 校验条目大小
            entry_size = len(str(entry_data.get("experience_data", {})))
            if entry_size > self.MAX_ENTRY_SIZE_BYTES:
                continue

            entry_id = entry_data.get("entry_id", f"L2-{uuid.uuid4().hex[:8]}")
            entry = {
                "entry_id": entry_id,
                "source_slot_id": entry_data.get("source_slot_id", ""),
                "experience_data": entry_data.get("experience_data", {}),
                "i_value": float(entry_data.get("i_value", 0)),
                "s_value": float(entry_data.get("s_value", 0)),
                "v_value": float(entry_data.get("v_value", 0)),
                "c_value": float(entry_data.get("c_value", 0)),
                "promoted_at": time.time(),
                "last_accessed_at": time.time(),
                "original_l1_timestamp": float(entry_data.get("timestamp", 0))
            }

            self._entries[entry_id] = entry
            self._entry_count += 1
            success_count += 1
            new_entry_ids.append(entry_id)

        if new_entry_ids:
            self._recent_7d_writes += success_count
            # 通知 L2 热度统计单元
            if self.bus:
                self.bus.publish_to_module(
                    target_module="ag-mem-23",
                    event_type="new_entries",
                    source_module=self.module_id,
                    data={"entry_ids": new_entry_ids}
                )

        # 回复 ag-mem-21
        if self.bus:
            self.bus.publish(
                topic="ag-mem-21.promotion_confirm",
                source_module=self.module_id,
                data={
                    "received_count": received,
                    "success_count": success_count,
                    "l2_usage_pct": self._calculate_usage_pct()
                },
                target_module="ag-mem-21",
                correlation_id=msg.correlation_id
            )

    def _handle_query(self, msg: Message):
        """处理来自分槽的查询请求"""
        source_slot = msg.data.get("source_slot", msg.source_module)
        conditions = msg.data.get("query", {})
        keywords = conditions.get("keywords", [])
        max_results = msg.data.get("max_results", 20)

        matched = []
        for entry in self._entries.values():
            # 分槽隔离：仅返回来源分槽匹配的条目 (S-03)
            if source_slot and entry.get("source_slot_id") != source_slot:
                continue

            if keywords:
                text = str(entry.get("experience_data", ""))
                if not any(kw in text for kw in keywords):
                    continue

            entry["last_accessed_at"] = time.time()
            matched.append(entry)

        matched.sort(key=lambda x: x.get("i_value", 0), reverse=True)
        matched = matched[:max_results]

        self._recent_7d_queries += 1

        # 向 ag-mem-23 推送命中条目列表
        if self.bus and matched:
            hit_entries = [
                {"entry_id": e["entry_id"], "query_source_slot": source_slot}
                for e in matched
            ]
            self.bus.publish_to_module(
                target_module="ag-mem-23",
                event_type="hit_entries",
                source_module=self.module_id,
                data={"hits": hit_entries}
            )

        # 返回查询结果给请求方
        if self.bus:
            self.bus.publish(
                topic=f"{msg.source_module}.query_response",
                source_module=self.module_id,
                data={
                    "matched_experiences": matched,
                    "layer": "L2",
                    "total_count": len(matched)
                },
                target_module=msg.source_module,
                correlation_id=msg.correlation_id
            )

    def _handle_overdue_entries(self):
        """处理超期条目 (S-02)：超过7天的晋升至L3或清除"""
        now = time.time()
        overdue_ids = []
        for eid, entry in list(self._entries.items()):
            retention_hours = (now - entry.get("promoted_at", now)) / 3600.0
            if retention_hours > self.MAX_RETENTION_HOURS:
                overdue_ids.append(eid)

        for eid in overdue_ids:
            entry = self._entries.pop(eid, None)
            if entry:
                self._entry_count -= 1
                # 高 I 值条目尝试晋升至 L3
                if entry.get("i_value", 0) >= 0.6:
                    if self.bus:
                        self.bus.publish_to_module(
                            target_module="ag-mem-24",
                            event_type="promoted_entries",
                            source_module=self.module_id,
                            data={"entries": [entry]}
                        )
                else:
                    # 低 I 值条目直接清除
                    if self.bus:
                        self.bus.publish_to_module(
                            target_module="ag-mem-42",
                            event_type="clear_entries",
                            source_module=self.module_id,
                            data={"entries": [entry], "reason": "L2超期清除"}
                        )

    def _force_cleanup_overdue(self):
        """强制清理超期条目"""
        now = time.time()
        for eid, entry in list(self._entries.items()):
            if (now - entry.get("promoted_at", now)) / 3600.0 > self.MAX_RETENTION_HOURS:
                del self._entries[eid]
                self._entry_count -= 1

    def _force_cleanup_low_i(self):
        """强制清理低重要度条目"""
        if self._entry_count == 0:
            return
        sorted_entries = sorted(self._entries.items(), key=lambda x: x[1].get("i_value", 0))
        to_remove = max(1, int(len(sorted_entries) * 0.10))
        for i in range(to_remove):
            eid = sorted_entries[i][0]
            del self._entries[eid]
            self._entry_count -= 1

    def _cleanup_expired_low_i(self):
        """清理超期且低重要度条目"""
        now = time.time()
        for eid, entry in list(self._entries.items()):
            retention_hours = (now - entry.get("promoted_at", now)) / 3600.0
            if retention_hours > self.MAX_RETENTION_HOURS and entry.get("i_value", 0) < 0.20:
                del self._entries[eid]
                self._entry_count -= 1

    def _calculate_usage_pct(self) -> float:
        if self.MAX_ENTRIES == 0:
            return 0.0
        return min(self._entry_count / self.MAX_ENTRIES, 1.0)

    def _report_status(self):
        if self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-48",
                event_type="storage_status",
                source_module=self.module_id,
                data={
                    "state": self.state.value,
                    "total_entries": self._entry_count,
                    "usage_pct": self._calculate_usage_pct(),
                    "recent_7d_writes": self._recent_7d_writes,
                    "recent_7d_queries": self._recent_7d_queries
                }
            )

    def get_entry_count(self) -> int:
        return self._entry_count

    # ====================== 管理接口 ======================
    def emergency_shutdown(self):
        self.state = StorageState.SYSTEM_PAUSED
        self._entries.clear()
        self._entry_count = 0
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