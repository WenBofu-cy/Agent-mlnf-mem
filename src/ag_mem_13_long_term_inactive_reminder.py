#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-13
模块名称: 画像槽长期未活跃提醒单元
所属分区: 二、漏斗一：用户画像漏斗
核心职责: 定期扫描漏斗一中所有长期槽的最后活跃时间戳，检测超过90天未活跃的画像槽。
          对于符合条件的槽位，通过 ECC 社会心智模块（ag-ecc-10）向用户发出提醒通知。
          若用户确认保留，重置活跃时间戳；若用户选择删除或超时未响应，向 ag-mem-02
          发起槽位清除请求。不参与任何认知决策，仅负责未活跃检测与提醒触发。

依赖模块:
    ag-mem-02(漏斗一专属调度单元), ag-mem-06(画像槽数据隔离管控单元)
被依赖模块:
    ag-ecc-10(社会心智模块), ag-mem-02, ag-mem-51(记忆变更日志追溯单元)

安全约束:
  S-01: 未活跃提醒通知仅通过 ag-ecc-10 发送，不得包含用户的任何画像数据内容
  S-02: 用户选择删除时，必须通过 ag-mem-02 发起正式的槽位清除流程，由 ag-mem-12 执行安全擦除
  S-03: 超时默认保留策略优先于删除，避免因用户未看到通知而导致数据丢失
  S-04: 提醒记录表仅存于内存，不持久化，系统重启后重新评估未活跃状态

版本: V1.0 (最终修复版)
"""

import time
import uuid
from typing import Any, Dict, List, Optional
from enum import Enum

from memory_bus import InternalBus, Message


class ReminderState(Enum):
    IDLE = "idle"
    SCANNING = "scanning"
    AWAITING_USER = "awaiting_user"
    PROCESSING_DECISION = "processing_decision"
    SYSTEM_PAUSED = "system_paused"


class LongTermInactiveReminder:
    module_id = "ag-mem-13"
    module_name = "画像槽长期未活跃提醒单元"
    version = "V1.0"

    INACTIVE_THRESHOLD_DAYS = 90
    SCAN_INTERVAL_HOURS = 24
    RESPONSE_TIMEOUT_DAYS = 7

    def __init__(self):
        self.bus: Optional[InternalBus] = None
        self.state = ReminderState.IDLE
        self._reminder_records: Dict[str, Dict[str, Any]] = {}
        self._cached_slots: List[Dict[str, Any]] = []
        self._last_scan_time: float = 0.0
        self._pending_logs: List[Dict[str, Any]] = []

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    # ====================== 全系统统一主循环入口 ======================
    def run_cycle(self):
        self.inactive_slot_reminder_main_loop()

    def inactive_slot_reminder_main_loop(self):
        if self.state == ReminderState.SYSTEM_PAUSED:
            return

        if self.bus:
            self.bus.process_batch(10)

        now = time.time()

        # 定时扫描
        if now - self._last_scan_time >= self.SCAN_INTERVAL_HOURS * 3600:
            self._perform_scan()
            self._last_scan_time = now

        # 检查用户决策超时
        self._check_timeouts(now)

        # 状态自恢复：若没有待处理的提醒，返回空闲
        if self.state == ReminderState.AWAITING_USER or self.state == ReminderState.PROCESSING_DECISION:
            if not self._has_pending():
                self.state = ReminderState.IDLE

    # ====================== 总线消息入口 ======================
    def handle_message(self, msg: Message):
        if not isinstance(msg.data, dict):
            return

        # 接收来自 ag-mem-02 的长期槽列表（用于扫描）
        if msg.topic == "ag-mem-13.slot_list":
            self._cached_slots = msg.data.get("slots", [])
            self._log_event("SLOT_LIST_RECEIVED", {"count": len(self._cached_slots)})
            return

        # 接收用户决策（中文值）
        if msg.topic == "ag-mem-13.user_decision":
            self._handle_user_decision(msg.data)
            return

    def _perform_scan(self):
        """执行未活跃扫描"""
        self.state = ReminderState.SCANNING

        # 如果没有缓存的槽列表，请求 ag-mem-02 提供
        if not self._cached_slots:
            if self.bus:
                self.bus.publish_to_module(
                    target_module="ag-mem-02",
                    event_type="query_long_term_slots",
                    source_module=self.module_id,
                    data={}
                )
            self._log_event("SLOT_LIST_REQUESTED", {})
            self.state = ReminderState.IDLE
            return

        now = time.time()
        inactive_count = 0

        for slot in self._cached_slots:
            if not isinstance(slot, dict):
                continue
            last_active = slot.get("last_active_at", 0)
            inactive_days = (now - last_active) / 86400.0
            if inactive_days <= self.INACTIVE_THRESHOLD_DAYS:
                continue

            slot_id = slot.get("slot_id", "")
            # 避免重复提醒
            if slot_id in self._reminder_records and self._reminder_records[slot_id].get("status") == "等待中":
                continue

            inactive_count += 1
            self._reminder_records[slot_id] = {
                "slot_id": slot_id,
                "user_id": slot.get("user_id", ""),
                "sent_at": now,
                "status": "等待中"
            }

            # 发送提醒通知到 ag-ecc-10（S-01：不携带用户画像数据）
            if self.bus:
                self.bus.publish_to_module(
                    target_module="ag-ecc-10",
                    event_type="inactive_reminder",
                    source_module=self.module_id,
                    data={
                        "slot_id": slot_id,
                        "inactive_days": int(inactive_days),
                        "last_active_date": time.strftime("%Y-%m-%d", time.localtime(last_active))
                    }
                )
            self._log_event("REMINDER_SENT", {
                "slot_id": slot_id,
                "inactive_days": int(inactive_days)
            })

        self.state = ReminderState.AWAITING_USER if inactive_count > 0 else ReminderState.IDLE
        self._log_event("SCAN_COMPLETED", {
            "total_slots": len(self._cached_slots),
            "inactive_count": inactive_count
        })

    def _handle_user_decision(self, data: Dict[str, Any]):
        """处理用户决策（决策值：保留/删除/忽略）"""
        slot_id = data.get("slot_id", "")
        decision = data.get("decision", "保留")

        record = self._reminder_records.get(slot_id)
        if not record or record.get("status") != "等待中":
            self._log_event("INVALID_USER_DECISION", {
                "slot_id": slot_id,
                "reason": "无待处理的提醒记录"
            })
            return

        self.state = ReminderState.PROCESSING_DECISION

        if decision == "保留":
            # 重置活跃时间戳
            if self.bus:
                self.bus.publish_to_module(
                    target_module="ag-mem-02",
                    event_type="reset_active_time",
                    source_module=self.module_id,
                    data={"slot_id": slot_id}
                )
            record["status"] = "已保留"
            self._log_event("USER_DECISION_KEEP", {"slot_id": slot_id})
        elif decision == "删除":
            # 发起清除请求（S-02）
            if self.bus:
                self.bus.publish_to_module(
                    target_module="ag-mem-02",
                    event_type="request_cleanup",
                    source_module=self.module_id,
                    data={"slot_id": slot_id, "reason": "用户主动删除（未活跃提醒触发）"}
                )
            record["status"] = "已删除"
            self._log_event("USER_DECISION_DELETE", {"slot_id": slot_id})
        else:  # 忽略
            record["status"] = "已忽略"
            self._log_event("USER_DECISION_IGNORE", {"slot_id": slot_id})

        self.state = ReminderState.AWAITING_USER if self._has_pending() else ReminderState.IDLE

    def _check_timeouts(self, now: float):
        """超时默认保留（S-03）"""
        for slot_id, record in list(self._reminder_records.items()):
            if record.get("status") != "等待中":
                continue
            elapsed = (now - record.get("sent_at", 0)) / 86400.0
            if elapsed >= self.RESPONSE_TIMEOUT_DAYS:
                # 超时默认保留
                if self.bus:
                    self.bus.publish_to_module(
                        target_module="ag-mem-02",
                        event_type="reset_active_time",
                        source_module=self.module_id,
                        data={"slot_id": slot_id}
                    )
                record["status"] = "已超时默认保留"
                self._log_event("TIMEOUT_DEFAULT_KEEP", {"slot_id": slot_id})

    def _has_pending(self) -> bool:
        """检查是否有等待中的提醒"""
        return any(r.get("status") == "等待中" for r in self._reminder_records.values())

    # ====================== 管理接口 ======================
    def emergency_shutdown(self):
        self.state = ReminderState.SYSTEM_PAUSED
        self._reminder_records.clear()
        self._cached_slots.clear()
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