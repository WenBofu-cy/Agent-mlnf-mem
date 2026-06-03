#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-02
模块名称: 漏斗一专属调度单元
所属分区: 一、顶层总控中枢
核心职责: 作为漏斗一（用户画像漏斗）的专属调度单元，负责用户画像槽的全生命周期管理。
          接收 ag-mem-01 转发的用户画像操作请求，匹配已有画像槽或触发新槽创建。
          管理长期槽、临时槽和访客槽的激活、休眠与过期清理。
          确保不同用户的画像槽之间严格物理隔离，禁止跨槽数据访问。
          不参与任何认知决策，仅执行漏斗一内部资源的调度与管理。

依赖模块:
    ag-mem-01(总控漏斗F0), ag-mem-04(用户身份识别单元),
    ag-mem-05(画像槽创建与初始化单元), ag-mem-06(画像槽数据隔离管控单元),
    ag-mem-12(临时画像槽自动清除单元)
被依赖模块:
    ag-mem-01, ag-mem-04, ag-mem-07(用户行为观测记录单元)

安全约束:
  S-01: 不同用户的画像槽之间物理存储隔离，禁止任何形式的跨槽数据访问
  S-02: 临时槽和访客槽的清理必须执行安全擦除（覆写后删除），不可直接回收存储
  S-03: 长期槽的用户画像数据编译期禁止接入 Agent 自学习链路
  S-04: 本模块仅负责槽位调度，不直接操作用户画像数据内容
"""

from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
import time
import uuid


class DispatcherState(Enum):
    IDLE = "idle"
    LOOKUP = "lookup"
    CREATING = "creating"
    CLEANING = "cleaning"
    SYSTEM_PAUSED = "system_paused"


class SlotType(Enum):
    LONG_TERM = "long_term"
    TEMPORARY = "temporary"
    GUEST = "guest"


@dataclass
class UserProfileRequest:
    request_id: str = ""
    user_id: str = ""
    operation_type: str = "query"  # query / write
    slot_type: Optional[SlotType] = None
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


@dataclass
class SlotInfo:
    slot_id: str = ""
    user_id: str = ""
    slot_type: SlotType = SlotType.LONG_TERM
    created_at: float = field(default_factory=time.time)
    last_active_at: float = field(default_factory=time.time)
    is_active: bool = True


@dataclass
class SlotMatchResult:
    request_id: str = ""
    slot_id: str = ""
    user_id: str = ""
    slot_type: SlotType = SlotType.LONG_TERM
    is_new: bool = False
    success: bool = True
    error_reason: str = ""


@dataclass
class SlotStatusReport:
    active_slots: int = 0
    long_term_count: int = 0
    temporary_count: int = 0
    guest_count: int = 0
    total_usage_bytes: int = 0


class FunnelOneDispatcher:
    MAX_LONG_TERM_SLOTS = 6
    MAX_TEMPORARY_SLOTS = 2
    MAX_GUEST_SLOTS = 1
    TEMP_SLOT_LIFETIME_SEC = 7 * 24 * 3600  # 7天
    CLEANUP_INTERVAL_SEC = 3600  # 1小时

    def __init__(self):
        self.module_id = "ag-mem-02"
        self.module_name = "漏斗一专属调度单元"
        self.version = "V1.0"

        self.state = DispatcherState.IDLE
        self._slot_map: Dict[str, SlotInfo] = {}  # user_id -> SlotInfo
        self._active_slot_per_session: Dict[str, str] = {}  # session_id -> slot_id
        self._slot_counters = {SlotType.LONG_TERM: 0, SlotType.TEMPORARY: 0, SlotType.GUEST: 0}
        self._last_cleanup_time = time.time()
        self._pending_logs: List[Dict[str, Any]] = []

        # 回调注入
        self._query_profile_request = None
        self._query_identity_result = None
        self._publish_slot_result = None
        self._publish_create_command = None
        self._publish_activate_signal = None
        self._publish_cleanup_command = None
        self._publish_status_report = None
        self._publish_event_log = None

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    def set_profile_request_query(self, callback: Callable[[], Optional[UserProfileRequest]]):
        self._query_profile_request = callback

    def set_identity_result_query(self, callback: Callable[[], Optional[Dict[str, Any]]]):
        self._query_identity_result = callback

    def set_slot_result_publisher(self, callback: Callable[[SlotMatchResult], None]):
        self._publish_slot_result = callback

    def set_create_command_publisher(self, callback: Callable[[str, Dict[str, Any]], None]):
        self._publish_create_command = callback

    def set_activate_signal_publisher(self, callback: Callable[[str, Dict[str, Any]], None]):
        self._publish_activate_signal = callback

    def set_cleanup_command_publisher(self, callback: Callable[[str, Dict[str, Any]], None]):
        self._publish_cleanup_command = callback

    def set_status_report_publisher(self, callback: Callable[[SlotStatusReport], None]):
        self._publish_status_report = callback

    def set_event_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_event_log = callback

    def run_dispatcher_cycle(self) -> Optional[SlotMatchResult]:
        now = time.time()

        if self.state == DispatcherState.SYSTEM_PAUSED:
            return None

        # 定期清理
        if now - self._last_cleanup_time >= self.CLEANUP_INTERVAL_SEC:
            self._perform_cleanup()
            self._last_cleanup_time = now

        # 接收请求
        request = self._query_profile_request() if self._query_profile_request else None
        if request is None:
            return None

        self.state = DispatcherState.LOOKUP
        result = self._handle_request(request)
        self.state = DispatcherState.IDLE
        return result

    def _handle_request(self, request: UserProfileRequest) -> SlotMatchResult:
        user_id = request.user_id
        if not user_id:
            return SlotMatchResult(
                request_id=request.request_id,
                success=False,
                error_reason="用户ID不能为空"
            )

        # 1. 查找已有槽位
        existing_slot = self._slot_map.get(user_id)
        if existing_slot and existing_slot.is_active:
            existing_slot.last_active_at = time.time()
            result = SlotMatchResult(
                request_id=request.request_id,
                slot_id=existing_slot.slot_id,
                user_id=user_id,
                slot_type=existing_slot.slot_type,
                is_new=False
            )
            self._publish_activate_signal("ag-mem-04", {"slot_id": existing_slot.slot_id, "user_id": user_id})
            if self._publish_activate_signal:
                self._publish_activate_signal("ag-mem-07", {"slot_id": existing_slot.slot_id, "user_id": user_id})
            if self._publish_slot_result:
                self._publish_slot_result(result)
            return result

        # 2. 如果是查询操作且无槽位，返回空
        if request.operation_type == "query":
            result = SlotMatchResult(
                request_id=request.request_id,
                success=True,
                error_reason="用户画像槽不存在"
            )
            if self._publish_slot_result:
                self._publish_slot_result(result)
            return result

        # 3. 写入请求，需要创建新槽
        slot_type = self._determine_slot_type(request)
        if slot_type == SlotType.LONG_TERM and self._slot_counters[SlotType.LONG_TERM] >= self.MAX_LONG_TERM_SLOTS:
            result = SlotMatchResult(
                request_id=request.request_id,
                success=False,
                error_reason="长期槽数量已达上限"
            )
            if self._publish_slot_result:
                self._publish_slot_result(result)
            return result

        if slot_type == SlotType.TEMPORARY and self._slot_counters[SlotType.TEMPORARY] >= self.MAX_TEMPORARY_SLOTS:
            slot_type = SlotType.GUEST  # 降级为访客槽

        # 4. 创建新槽
        self.state = DispatcherState.CREATING
        new_slot_id = f"SLOT-{slot_type.value.upper()}-{uuid.uuid4().hex[:8]}"
        new_slot = SlotInfo(
            slot_id=new_slot_id,
            user_id=user_id,
            slot_type=slot_type
        )
        self._slot_map[user_id] = new_slot
        self._slot_counters[slot_type] += 1

        # 通知 ag-mem-05 创建槽
        if self._publish_create_command:
            self._publish_create_command("ag-mem-05", {
                "user_id": user_id,
                "slot_type": slot_type.value,
                "slot_id": new_slot_id
            })

        # 激活信号
        if self._publish_activate_signal:
            self._publish_activate_signal("ag-mem-04", {"slot_id": new_slot_id, "user_id": user_id})
            self._publish_activate_signal("ag-mem-07", {"slot_id": new_slot_id, "user_id": user_id})

        result = SlotMatchResult(
            request_id=request.request_id,
            slot_id=new_slot_id,
            user_id=user_id,
            slot_type=slot_type,
            is_new=True
        )
        if self._publish_slot_result:
            self._publish_slot_result(result)

        self._log_event("SLOT_CREATED", {"slot_id": new_slot_id, "user_id": user_id, "type": slot_type.value})
        return result

    def _determine_slot_type(self, request: UserProfileRequest) -> SlotType:
        if request.slot_type:
            return request.slot_type
        # 从身份识别结果判断
        identity = self._query_identity_result() if self._query_identity_result else {}
        if identity.get("confidence", 0) >= 0.85:
            return SlotType.LONG_TERM
        elif identity.get("confidence", 0) >= 0.5:
            return SlotType.TEMPORARY
        else:
            return SlotType.GUEST

    def _perform_cleanup(self):
        self.state = DispatcherState.CLEANING
        now = time.time()
        expired_users = []
        for user_id, slot in self._slot_map.items():
            if slot.slot_type == SlotType.GUEST:
                expired_users.append(user_id)
            elif slot.slot_type == SlotType.TEMPORARY and (now - slot.created_at) > self.TEMP_SLOT_LIFETIME_SEC:
                expired_users.append(user_id)

        for user_id in expired_users:
            slot = self._slot_map.pop(user_id, None)
            if slot:
                self._slot_counters[slot.slot_type] -= 1
                if self._publish_cleanup_command:
                    self._publish_cleanup_command("ag-mem-12", {"slot_id": slot.slot_id, "user_id": user_id})
                self._log_event("SLOT_CLEANED", {"slot_id": slot.slot_id, "user_id": user_id, "type": slot.slot_type.value})

        self._publish_status_if_needed()

    def _publish_status_if_needed(self):
        if self._publish_status_report:
            self._publish_status_report(SlotStatusReport(
                active_slots=len(self._slot_map),
                long_term_count=self._slot_counters[SlotType.LONG_TERM],
                temporary_count=self._slot_counters[SlotType.TEMPORARY],
                guest_count=self._slot_counters[SlotType.GUEST]
            ))

    def get_state(self) -> DispatcherState:
        return self.state

    def emergency_shutdown(self):
        self.state = DispatcherState.SYSTEM_PAUSED
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
    print("  Agent-mlnf-mem 漏斗一专属调度单元 (ag-mem-02) 演示")
    print("=" * 70)

    dispatcher = FunnelOneDispatcher()
    dispatcher.set_identity_result_query(lambda: {"confidence": 0.9})

    print_separator("STEP 1: 新用户创建长期槽")
    dispatcher.set_profile_request_query(lambda: UserProfileRequest(
        request_id="REQ-001",
        user_id="U001",
        operation_type="write"
    ))
    result = dispatcher.run_dispatcher_cycle()
    if result:
        print(f"  槽位ID: {result.slot_id}")
        print(f"  槽位类型: {result.slot_type.value}")
        print(f"  是否新建: {result.is_new}")

    print_separator("STEP 2: 已有用户查询")
    dispatcher.set_profile_request_query(lambda: UserProfileRequest(
        request_id="REQ-002",
        user_id="U001",
        operation_type="query"
    ))
    result = dispatcher.run_dispatcher_cycle()
    if result:
        print(f"  槽位ID: {result.slot_id}")
        print(f"  是否新建: {result.is_new}")

    print_separator("STEP 3: 访客模式")
    dispatcher.set_identity_result_query(lambda: {"confidence": 0.3})
    dispatcher.set_profile_request_query(lambda: UserProfileRequest(
        request_id="REQ-003",
        user_id="GUEST_001",
        operation_type="write"
    ))
    result = dispatcher.run_dispatcher_cycle()
    if result:
        print(f"  槽位ID: {result.slot_id}")
        print(f"  槽位类型: {result.slot_type.value}")

    print("\n✅ 漏斗一专属调度单元演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("ag-mem-02 漏斗一专属调度单元 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        def setup_dispatcher(confidence=0.9):
            d = FunnelOneDispatcher()
            d.set_identity_result_query(lambda: {"confidence": confidence})
            return d

        # TC-M02-01: 新用户创建长期槽
        print("\n[TC-M02-01] 新用户创建长期槽")
        try:
            d = setup_dispatcher()
            d.set_profile_request_query(lambda: UserProfileRequest(
                request_id="T01", user_id="U001", operation_type="write"
            ))
            result = d.run_dispatcher_cycle()
            assert result is not None
            assert result.is_new
            assert result.slot_type == SlotType.LONG_TERM
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M02-02: 已有用户查询
        print("\n[TC-M02-02] 已有用户查询")
        try:
            d = setup_dispatcher()
            d.set_profile_request_query(lambda: UserProfileRequest(
                request_id="T02-1", user_id="U001", operation_type="write"
            ))
            d.run_dispatcher_cycle()
            d.set_profile_request_query(lambda: UserProfileRequest(
                request_id="T02-2", user_id="U001", operation_type="query"
            ))
            result = d.run_dispatcher_cycle()
            assert result is not None
            assert not result.is_new
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M02-03: 访客模式
        print("\n[TC-M02-03] 访客模式")
        try:
            d = setup_dispatcher(confidence=0.0)
            d.set_profile_request_query(lambda: UserProfileRequest(
                request_id="T03", user_id="GUEST_001", operation_type="write"
            ))
            result = d.run_dispatcher_cycle()
            assert result is not None
            assert result.slot_type == SlotType.GUEST
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M02-04: 长期槽数量达上限
        print("\n[TC-M02-04] 长期槽数量达上限")
        try:
            d = setup_dispatcher()
            # 填满6个长期槽
            for i in range(6):
                d._slot_map[f"USER_{i}"] = SlotInfo(
                    slot_id=f"SLOT-LONG-{i}",
                    user_id=f"USER_{i}",
                    slot_type=SlotType.LONG_TERM
                )
                d._slot_counters[SlotType.LONG_TERM] += 1
            d.set_profile_request_query(lambda: UserProfileRequest(
                request_id="T04", user_id="NEW_USER", operation_type="write"
            ))
            result = d.run_dispatcher_cycle()
            assert result is not None
            assert not result.success
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M02-05: 紧急熔断
        print("\n[TC-M02-05] 紧急熔断")
        try:
            d = setup_dispatcher()
            d.emergency_shutdown()
            assert d.state == DispatcherState.SYSTEM_PAUSED
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M02-06: 新用户查询无槽位
        print("\n[TC-M02-06] 新用户查询无槽位")
        try:
            d = setup_dispatcher()
            d.set_profile_request_query(lambda: UserProfileRequest(
                request_id="T06", user_id="UNKNOWN_USER", operation_type="query"
            ))
            result = d.run_dispatcher_cycle()
            assert result is not None
            assert result.success
            assert "不存在" in result.error_reason
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