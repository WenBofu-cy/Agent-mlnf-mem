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
"""

from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
import time
import uuid


class ReminderState(Enum):
    IDLE = "idle"
    SCANNING = "scanning"
    AWAITING_USER = "awaiting_user"
    PROCESSING_DECISION = "processing_decision"
    SYSTEM_PAUSED = "system_paused"


class UserDecision(Enum):
    KEEP = "保留"
    DELETE = "删除"
    IGNORE = "忽略"


@dataclass
class ScanTrigger:
    trigger_type: str = "定时"
    scan_scope: str = "全量"
    inactive_threshold_days: int = 90
    timestamp: float = field(default_factory=time.time)


@dataclass
class LongTermSlotInfo:
    slot_id: str = ""
    user_id: str = ""
    created_at: float = 0.0
    last_active_at: float = 0.0
    storage_usage_bytes: int = 0


@dataclass
class UserDecisionResponse:
    slot_id: str = ""
    decision: UserDecision = UserDecision.KEEP
    timestamp: float = field(default_factory=time.time)


@dataclass
class InactiveSlotNotification:
    notification_id: str = ""
    slot_id: str = ""
    user_id: str = ""
    inactive_days: int = 0
    last_active_date: str = ""
    storage_usage: str = ""
    options: List[str] = field(default_factory=lambda: ["保留", "删除", "忽略"])
    timestamp: float = field(default_factory=time.time)


@dataclass
class ScanResultReport:
    scan_time: float = field(default_factory=time.time)
    total_slots_scanned: int = 0
    inactive_slots_found: int = 0
    reminders_sent: int = 0
    timestamp: float = field(default_factory=time.time)


@dataclass
class ReminderRecord:
    slot_id: str = ""
    sent_at: float = 0.0
    response_status: str = "等待中"


class LongTermInactiveReminder:
    INACTIVE_THRESHOLD_DAYS = 90
    SCAN_INTERVAL_HOURS = 24
    RESPONSE_TIMEOUT_DAYS = 7
    REPORT_INTERVAL_SEC = 180

    def __init__(self):
        self.module_id = "ag-mem-13"
        self.module_name = "画像槽长期未活跃提醒单元"
        self.version = "V1.0"

        self.state = ReminderState.IDLE
        self._reminder_records: Dict[str, ReminderRecord] = {}
        self._last_scan_time: float = 0.0
        self._last_report_time: float = 0.0
        self._pending_logs: List[Dict[str, Any]] = []

        # 回调注入
        self._query_scan_trigger = None
        self._query_long_term_slots = None
        self._query_user_decision = None

        self._publish_notification = None
        self._publish_clear_request = None
        self._publish_reset_request = None
        self._publish_scan_result = None
        self._publish_event_log = None

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    def set_scan_trigger_query(self, callback: Callable[[], Optional[ScanTrigger]]):
        self._query_scan_trigger = callback

    def set_long_term_slots_query(self, callback: Callable[[], Optional[List[LongTermSlotInfo]]]):
        self._query_long_term_slots = callback

    def set_user_decision_query(self, callback: Callable[[], Optional[UserDecisionResponse]]):
        self._query_user_decision = callback

    def set_notification_publisher(self, callback: Callable[[InactiveSlotNotification], None]):
        self._publish_notification = callback

    def set_clear_request_publisher(self, callback: Callable[[str, str, str], None]):
        self._publish_clear_request = callback

    def set_reset_request_publisher(self, callback: Callable[[str, str], None]):
        self._publish_reset_request = callback

    def set_scan_result_publisher(self, callback: Callable[[ScanResultReport], None]):
        self._publish_scan_result = callback

    def set_event_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_event_log = callback

    def run_reminder_cycle(self):
        now = time.time()

        if self.state == ReminderState.SYSTEM_PAUSED:
            return

        # 处理用户决策
        if self.state == ReminderState.AWAITING_USER:
            self._check_user_decisions()
            self._check_timeouts(now)

        # 处理用户决策后的响应
        if self.state == ReminderState.PROCESSING_DECISION:
            self._check_pending_decisions()
            self.state = ReminderState.IDLE if not self._has_pending_responses() else ReminderState.AWAITING_USER

        # 定时扫描
        trigger = self._query_scan_trigger() if self._query_scan_trigger else None
        if trigger and self.state == ReminderState.IDLE:
            self._perform_scan()
        elif now - self._last_scan_time >= self.SCAN_INTERVAL_HOURS * 3600 and self.state == ReminderState.IDLE:
            self._perform_scan()

    def _perform_scan(self):
        self.state = ReminderState.SCANNING
        now = time.time()
        self._last_scan_time = now

        slots = self._query_long_term_slots() if self._query_long_term_slots else []
        if not slots:
            self.state = ReminderState.IDLE
            return

        inactive_count = 0
        for slot in slots:
            inactive_days = (now - slot.last_active_at) / 86400.0
            if inactive_days <= self.INACTIVE_THRESHOLD_DAYS:
                continue

            # 跳过已有未处理提醒的槽位
            if slot.slot_id in self._reminder_records:
                record = self._reminder_records[slot.slot_id]
                if record.response_status == "等待中":
                    continue

            inactive_count += 1
            notification = InactiveSlotNotification(
                notification_id=f"NOTIFY-{uuid.uuid4().hex[:8]}",
                slot_id=slot.slot_id,
                user_id=slot.user_id,
                inactive_days=int(inactive_days),
                last_active_date=time.strftime("%Y-%m-%d", time.localtime(slot.last_active_at)),
                storage_usage=f"{slot.storage_usage_bytes / 1024:.0f}KB"
            )

            if self._publish_notification:
                self._publish_notification(notification)

            self._reminder_records[slot.slot_id] = ReminderRecord(
                slot_id=slot.slot_id,
                sent_at=now,
                response_status="等待中"
            )

        if self._publish_scan_result:
            self._publish_scan_result(ScanResultReport(
                total_slots_scanned=len(slots),
                inactive_slots_found=inactive_count,
                reminders_sent=inactive_count
            ))

        self.state = ReminderState.AWAITING_USER if inactive_count > 0 else ReminderState.IDLE

    def _check_user_decisions(self):
        decision = self._query_user_decision() if self._query_user_decision else None
        if decision is None:
            return

        if decision.slot_id not in self._reminder_records:
            return

        record = self._reminder_records[decision.slot_id]
        if record.response_status != "等待中":
            return

        self.state = ReminderState.PROCESSING_DECISION

        if decision.decision == UserDecision.KEEP:
            self._handle_keep(decision.slot_id)
            record.response_status = "已保留"
        elif decision.decision == UserDecision.DELETE:
            self._handle_delete(decision.slot_id)
            record.response_status = "已删除"
        elif decision.decision == UserDecision.IGNORE:
            record.response_status = "已忽略"

    def _check_timeouts(self, now: float):
        for slot_id, record in list(self._reminder_records.items()):
            if record.response_status != "等待中":
                continue
            elapsed_days = (now - record.sent_at) / 86400.0
            if elapsed_days >= self.RESPONSE_TIMEOUT_DAYS:
                self._handle_keep(slot_id)
                record.response_status = "已超时默认保留"
                self._log_event("TIMEOUT_DEFAULT_KEEP", {"slot_id": slot_id})

    def _handle_keep(self, slot_id: str):
        if self._publish_reset_request:
            self._publish_reset_request(slot_id, "用户确认保留")
        self._log_event("USER_KEPT_SLOT", {"slot_id": slot_id})

    def _handle_delete(self, slot_id: str):
        if self._publish_clear_request:
            self._publish_clear_request(slot_id, "用户主动删除（未活跃提醒触发）", "长期槽删除")
        self._log_event("USER_DELETED_SLOT", {"slot_id": slot_id})

    def _check_pending_decisions(self):
        for slot_id in list(self._reminder_records.keys()):
            record = self._reminder_records[slot_id]
            if record.response_status not in ("等待中", "已忽略"):
                del self._reminder_records[slot_id]

    def _has_pending_responses(self) -> bool:
        return any(r.response_status == "等待中" for r in self._reminder_records.values())

    def get_state(self) -> ReminderState:
        return self.state

    def emergency_shutdown(self):
        self.state = ReminderState.SYSTEM_PAUSED
        print(f"[{self.module_id}] 紧急熔断")

    def _log_event(self, event_type: str, details: Dict[str, Any]):
        entry = {
            "log_id": f"log-{uuid.uuid4().hex[:8]}",
            "event_type": event_type,
            "source_module": self.module_id,
            "details": details,
            "timestamp": time.time()
        }
        self._pending_logs.append(entry)
        if self._publish_event_log:
            self._publish_event_log(entry)

    def collect_pending_logs(self) -> List[Dict[str, Any]]:
        logs = self._pending_logs.copy()
        self._pending_logs.clear()
        return logs


def print_separator(title: str):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def demo_main():
    print("=" * 70)
    print("  Agent-mlnf-mem 画像槽长期未活跃提醒单元 (ag-mem-13) 演示")
    print("=" * 70)

    reminder = LongTermInactiveReminder()
    old_time = time.time() - 100 * 86400  # 100天前

    reminder.set_long_term_slots_query(lambda: [
        LongTermSlotInfo(slot_id="SLOT-LONG-0001", user_id="U001", last_active_at=old_time, storage_usage_bytes=3*1024*1024),
        LongTermSlotInfo(slot_id="SLOT-LONG-0002", user_id="U002", last_active_at=time.time(), storage_usage_bytes=1*1024*1024),
    ])

    print_separator("STEP 1: 定时扫描（检测到1个未活跃槽）")
    reminder._perform_scan()
    print(f"  状态: {reminder.state.value}")
    print(f"  提醒记录数: {len(reminder._reminder_records)}")

    print_separator("STEP 2: 用户选择保留")
    reminder.set_user_decision_query(lambda: UserDecisionResponse(
        slot_id="SLOT-LONG-0001", decision=UserDecision.KEEP
    ))
    reminder.run_reminder_cycle()
    print(f"  状态: {reminder.state.value}")

    print_separator("STEP 3: 超时默认保留")
    reminder._reminder_records["SLOT-LONG-0003"] = ReminderRecord(
        slot_id="SLOT-LONG-0003",
        sent_at=time.time() - 8 * 86400  # 8天前
    )
    reminder.run_reminder_cycle()
    print(f"  SLOT-LONG-0003 状态: {reminder._reminder_records.get('SLOT-LONG-0003', ReminderRecord()).response_status}")

    print("\n✅ 画像槽长期未活跃提醒单元演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("ag-mem-13 画像槽长期未活跃提醒单元 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        def setup_reminder():
            r = LongTermInactiveReminder()
            r.set_long_term_slots_query(lambda: [
                LongTermSlotInfo(slot_id="SLOT-LONG-0001", user_id="U001", last_active_at=time.time() - 100 * 86400),
                LongTermSlotInfo(slot_id="SLOT-LONG-0002", user_id="U002", last_active_at=time.time()),
            ])
            return r

        # TC-M13-01: 扫描检测到未活跃槽
        print("\n[TC-M13-01] 扫描检测到未活跃槽")
        try:
            r = setup_reminder()
            r._perform_scan()
            assert r.state == ReminderState.AWAITING_USER
            assert len(r._reminder_records) == 1
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M13-02: 用户选择保留
        print("\n[TC-M13-02] 用户选择保留")
        try:
            r = setup_reminder()
            r._perform_scan()
            r.set_user_decision_query(lambda: UserDecisionResponse(slot_id="SLOT-LONG-0001", decision=UserDecision.KEEP))
            r.run_reminder_cycle()
            record = r._reminder_records.get("SLOT-LONG-0001")
            assert record is not None
            assert record.response_status == "已保留"
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M13-03: 用户选择删除
        print("\n[TC-M13-03] 用户选择删除")
        try:
            r = setup_reminder()
            r._perform_scan()
            r.set_user_decision_query(lambda: UserDecisionResponse(slot_id="SLOT-LONG-0001", decision=UserDecision.DELETE))
            r.run_reminder_cycle()
            record = r._reminder_records.get("SLOT-LONG-0001")
            assert record is not None
            assert record.response_status == "已删除"
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M13-04: 超时默认保留
        print("\n[TC-M13-04] 超时默认保留")
        try:
            r = setup_reminder()
            r._reminder_records["SLOT-OLD"] = ReminderRecord(
                slot_id="SLOT-OLD",
                sent_at=time.time() - 8 * 86400,
                response_status="等待中"
            )
            r._check_timeouts(time.time())
            record = r._reminder_records.get("SLOT-OLD")
            assert record is not None
            assert record.response_status == "已超时默认保留"
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M13-05: 全活跃槽不触发提醒
        print("\n[TC-M13-05] 全活跃槽不触发提醒")
        try:
            r = LongTermInactiveReminder()
            r.set_long_term_slots_query(lambda: [
                LongTermSlotInfo(slot_id="S1", user_id="U1", last_active_at=time.time()),
                LongTermSlotInfo(slot_id="S2", user_id="U2", last_active_at=time.time()),
            ])
            r._perform_scan()
            assert r.state == ReminderState.IDLE
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M13-06: 紧急熔断
        print("\n[TC-M13-06] 紧急熔断")
        try:
            r = setup_reminder()
            r.emergency_shutdown()
            assert r.state == ReminderState.SYSTEM_PAUSED
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        print("\n" + "=" * 60)
        print(f"测试结果: {passed} PASS, {failed} FAIL")
        print("=" * 60)
    else:
        demo_main()
```