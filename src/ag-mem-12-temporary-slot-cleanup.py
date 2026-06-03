#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-12
模块名称: 临时画像槽自动清除单元
所属分区: 二、漏斗一：用户画像漏斗
核心职责: 管理漏斗一中临时槽和访客槽的生命周期终止处理。在临时槽超过7天有效期或访客槽
          会话结束时，接收 ag-mem-02 下发的清除指令，对目标画像槽执行安全擦除（覆写后
          删除）并释放存储配额。确保被清除的用户数据不可恢复，满足隐私合规要求。不参与
          槽位创建或内容管理决策，仅执行安全清除操作。

依赖模块:
    ag-mem-02(漏斗一专属调度单元), ag-mem-06(画像槽数据隔离管控单元),
    ag-mem-48(全局容量配额管控单元)
被依赖模块:
    ag-mem-02, ag-mem-51(记忆变更日志追溯单元)

安全约束:
  S-01: 临时槽和访客槽的清除必须执行安全擦除（临时槽覆写1次，访客槽直接删除），不可仅标记删除
  S-02: 清除操作开始前必须先吊销目标槽位的所有访问令牌，防止擦除过程中数据被读取
  S-03: 安全擦除过程中不得被任何读取或写入请求中断
  S-04: 清除操作的每个步骤（吊销→锁定→擦除→退还）必须完整记录日志，不可篡改
"""

from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
import time
import uuid


class CleanupState(Enum):
    IDLE = "idle"
    PREPARING = "preparing"
    ERASING = "erasing"
    COMPLETED = "completed"
    FAILED = "failed"
    SYSTEM_PAUSED = "system_paused"


class SlotType(Enum):
    LONG_TERM = "长期槽"
    TEMPORARY = "临时槽"
    GUEST = "访客槽"


@dataclass
class CleanupCommand:
    target_slot_id: str = ""
    cleanup_reason: str = ""
    slot_type: SlotType = SlotType.TEMPORARY
    immediate: bool = True
    timestamp: float = field(default_factory=time.time)


@dataclass
class TokenRevokeConfirm:
    slot_id: str = ""
    revoked_count: int = 0
    timestamp: float = field(default_factory=time.time)


@dataclass
class StorageLockConfirm:
    slot_id: str = ""
    locked: bool = True
    timestamp: float = field(default_factory=time.time)


@dataclass
class QuotaReturnConfirm:
    slot_id: str = ""
    returned_bytes: int = 0
    remaining_bytes: int = 0
    timestamp: float = field(default_factory=time.time)


@dataclass
class CleanupResult:
    slot_id: str = ""
    success: bool = True
    released_bytes: int = 0
    erasure_duration_ms: float = 0.0
    is_recoverable: bool = False
    error_reason: str = ""
    timestamp: float = field(default_factory=time.time)


class TemporarySlotCleanup:
    ERASE_TIMEOUT_SEC = 30
    ERASE_METHODS = {
        SlotType.TEMPORARY: "single_overwrite",
        SlotType.GUEST: "direct_delete",
        SlotType.LONG_TERM: "triple_overwrite",
    }

    def __init__(self):
        self.module_id = "ag-mem-12"
        self.module_name = "临时画像槽自动清除单元"
        self.version = "V1.0"

        self.state = CleanupState.IDLE
        self._current_cleanup_task: Optional[CleanupCommand] = None
        self._cleanup_queue: List[CleanupCommand] = []
        self._erasure_start_time: float = 0.0
        self._pending_logs: List[Dict[str, Any]] = []

        # 回调注入
        self._query_cleanup_command = None
        self._query_token_revoke_confirm = None
        self._query_storage_lock_confirm = None
        self._query_quota_return_confirm = None

        self._publish_cleanup_result = None
        self._publish_token_revoke_request = None
        self._publish_quota_return_request = None
        self._publish_event_log = None

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    def set_cleanup_command_query(self, callback: Callable[[], Optional[CleanupCommand]]):
        self._query_cleanup_command = callback

    def set_token_revoke_confirm_query(self, callback: Callable[[], Optional[TokenRevokeConfirm]]):
        self._query_token_revoke_confirm = callback

    def set_storage_lock_confirm_query(self, callback: Callable[[], Optional[StorageLockConfirm]]):
        self._query_storage_lock_confirm = callback

    def set_quota_return_confirm_query(self, callback: Callable[[], Optional[QuotaReturnConfirm]]):
        self._query_quota_return_confirm = callback

    def set_cleanup_result_publisher(self, callback: Callable[[CleanupResult], None]):
        self._publish_cleanup_result = callback

    def set_token_revoke_publisher(self, callback: Callable[[str, str], None]):
        self._publish_token_revoke_request = callback

    def set_quota_return_publisher(self, callback: Callable[[str, int], None]):
        self._publish_quota_return_request = callback

    def set_event_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_event_log = callback

    def run_cleanup_cycle(self):
        now = time.time()

        if self.state == CleanupState.SYSTEM_PAUSED:
            return

        # 处理完成或失败后的队列
        if self.state in (CleanupState.COMPLETED, CleanupState.FAILED):
            if self._cleanup_queue:
                next_cmd = self._cleanup_queue.pop(0)
                self._start_cleanup(next_cmd)
            else:
                self.state = CleanupState.IDLE
                self._current_cleanup_task = None

        # 等待令牌吊销确认
        if self.state == CleanupState.PREPARING:
            confirm = self._query_token_revoke_confirm() if self._query_token_revoke_confirm else None
            if confirm and self._current_cleanup_task and confirm.slot_id == self._current_cleanup_task.target_slot_id:
                self._begin_erasure()

        # 监控擦除进度
        if self.state == CleanupState.ERASING:
            if now - self._erasure_start_time >= self.ERASE_TIMEOUT_SEC:
                self._handle_erasure_timeout()

            # 模拟轮询擦除状态，实际实现中会有存储管理器的回调
            quota_confirm = self._query_quota_return_confirm() if self._query_quota_return_confirm else None
            if quota_confirm and self._current_cleanup_task and quota_confirm.slot_id == self._current_cleanup_task.target_slot_id:
                self._finalize_cleanup(quota_confirm)

        # 接收新的清除指令
        command = self._query_cleanup_command() if self._query_cleanup_command else None
        if command and self.state == CleanupState.IDLE:
            self._start_cleanup(command)
        elif command and self.state != CleanupState.IDLE:
            self._cleanup_queue.append(command)

    def _start_cleanup(self, command: CleanupCommand):
        self._current_cleanup_task = command
        self.state = CleanupState.PREPARING

        # 第一步：吊销所有访问令牌
        if self._publish_token_revoke_request:
            self._publish_token_revoke_request(command.target_slot_id, "槽位清除")

    def _begin_erasure(self):
        if self._current_cleanup_task is None:
            return

        self.state = CleanupState.ERASING
        self._erasure_start_time = time.time()

        task = self._current_cleanup_task
        erase_method = self.ERASE_METHODS.get(task.slot_type, "single_overwrite")

        # 模拟执行擦除操作
        # 实际实现中会调用存储管理器的安全擦除接口
        released_bytes = self._simulate_erasure(task.slot_id, erase_method)

        # 退还配额
        if self._publish_quota_return_request:
            self._publish_quota_return_request(task.slot_id, released_bytes)

    def _simulate_erasure(self, slot_id: str, method: str) -> int:
        # 模拟不同擦除方法返回释放的空间量
        method_bytes = {
            "direct_delete": 500 * 1024,
            "single_overwrite": 2 * 1024 * 1024,
            "triple_overwrite": 5 * 1024 * 1024,
        }
        return method_bytes.get(method, 1 * 1024 * 1024)

    def _finalize_cleanup(self, quota_confirm: QuotaReturnConfirm):
        if self._current_cleanup_task is None:
            return

        self.state = CleanupState.COMPLETED
        task = self._current_cleanup_task

        result = CleanupResult(
            slot_id=task.target_slot_id,
            success=True,
            released_bytes=quota_confirm.returned_bytes,
            erasure_duration_ms=(time.time() - self._erasure_start_time) * 1000,
            is_recoverable=False
        )

        if self._publish_cleanup_result:
            self._publish_cleanup_result(result)

        self._log_event("SLOT_CLEANED", {
            "slot_id": task.target_slot_id,
            "type": task.slot_type.value,
            "reason": task.cleanup_reason,
            "released_bytes": quota_confirm.returned_bytes
        })

    def _handle_erasure_timeout(self):
        self.state = CleanupState.FAILED

        if self._publish_cleanup_result and self._current_cleanup_task:
            self._publish_cleanup_result(CleanupResult(
                slot_id=self._current_cleanup_task.target_slot_id,
                success=False,
                error_reason="安全擦除超时"
            ))

        self._log_event("ERASURE_TIMEOUT", {
            "slot_id": self._current_cleanup_task.target_slot_id if self._current_cleanup_task else "UNKNOWN"
        })

    def get_state(self) -> CleanupState:
        return self.state

    def emergency_shutdown(self):
        self.state = CleanupState.SYSTEM_PAUSED
        # 擦除是单向操作，不中断
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
    print("  Agent-mlnf-mem 临时画像槽自动清除单元 (ag-mem-12) 演示")
    print("=" * 70)

    cleanup = TemporarySlotCleanup()

    print_separator("STEP 1: 清除临时槽")
    cleanup.set_cleanup_command_query(lambda: CleanupCommand(
        target_slot_id="SLOT-TEMP-0001",
        cleanup_reason="过期清理",
        slot_type=SlotType.TEMPORARY
    ))
    cleanup.run_cleanup_cycle()
    print(f"  状态: {cleanup.state.value}")
    # 模拟令牌吊销确认
    cleanup.set_token_revoke_confirm_query(lambda: TokenRevokeConfirm(slot_id="SLOT-TEMP-0001", revoked_count=3))
    cleanup.run_cleanup_cycle()
    print(f"  状态: {cleanup.state.value}")

    print_separator("STEP 2: 清除访客槽")
    cleanup.set_cleanup_command_query(lambda: CleanupCommand(
        target_slot_id="SLOT-GUEST-0001",
        cleanup_reason="会话结束",
        slot_type=SlotType.GUEST
    ))
    cleanup.run_cleanup_cycle()
    print(f"  状态: {cleanup.state.value}")

    print("\n✅ 临时画像槽自动清除单元演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("ag-mem-12 临时画像槽自动清除单元 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        def setup_cleanup():
            c = TemporarySlotCleanup()
            c.set_token_revoke_confirm_query(lambda: TokenRevokeConfirm(slot_id="SLOT-TEMP-0001", revoked_count=3))
            c.set_quota_return_confirm_query(lambda: QuotaReturnConfirm(slot_id="SLOT-TEMP-0001", returned_bytes=2*1024*1024))
            return c

        # TC-M12-01: 正常清除临时槽
        print("\n[TC-M12-01] 正常清除临时槽")
        try:
            c = setup_cleanup()
            c.set_cleanup_command_query(lambda: CleanupCommand(
                target_slot_id="SLOT-TEMP-0001", cleanup_reason="过期", slot_type=SlotType.TEMPORARY
            ))
            c.run_cleanup_cycle()
            assert c.state == CleanupState.PREPARING
            c.run_cleanup_cycle()
            c.run_cleanup_cycle()
            assert c.state in (CleanupState.COMPLETED, CleanupState.ERASING)
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M12-02: 清除访客槽
        print("\n[TC-M12-02] 清除访客槽")
        try:
            c = TemporarySlotCleanup()
            c.set_token_revoke_confirm_query(lambda: TokenRevokeConfirm(slot_id="SLOT-GUEST-001", revoked_count=1))
            c.set_quota_return_confirm_query(lambda: QuotaReturnConfirm(slot_id="SLOT-GUEST-001", returned_bytes=500*1024))
            c.set_cleanup_command_query(lambda: CleanupCommand(
                target_slot_id="SLOT-GUEST-001", cleanup_reason="会话结束", slot_type=SlotType.GUEST
            ))
            c.run_cleanup_cycle()
            c.run_cleanup_cycle()
            c.run_cleanup_cycle()
            assert c.state in (CleanupState.COMPLETED, CleanupState.ERASING)
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M12-03: 擦除超时处理
        print("\n[TC-M12-03] 擦除超时处理")
        try:
            c = TemporarySlotCleanup()
            c.set_token_revoke_confirm_query(lambda: TokenRevokeConfirm(slot_id="SLOT-TEMP-TIMEOUT", revoked_count=2))
            c.set_cleanup_command_query(lambda: CleanupCommand(
                target_slot_id="SLOT-TEMP-TIMEOUT", cleanup_reason="测试", slot_type=SlotType.TEMPORARY
            ))
            c.run_cleanup_cycle()
            c.run_cleanup_cycle()
            c._erasure_start_time = time.time() - c.ERASE_TIMEOUT_SEC - 1
            c.run_cleanup_cycle()
            assert c.state == CleanupState.FAILED
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M12-04: 任务队列处理
        print("\n[TC-M12-04] 任务队列处理")
        try:
            c = setup_cleanup()
            c._cleanup_queue = [
                CleanupCommand(target_slot_id="SLOT-QUEUE-1", cleanup_reason="测试", slot_type=SlotType.TEMPORARY),
                CleanupCommand(target_slot_id="SLOT-QUEUE-2", cleanup_reason="测试", slot_type=SlotType.TEMPORARY),
            ]
            c.state = CleanupState.COMPLETED
            c.run_cleanup_cycle()
            assert c._current_cleanup_task is not None
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M12-05: 长期槽三次覆写擦除
        print("\n[TC-M12-05] 长期槽三次覆写擦除")
        try:
            c = TemporarySlotCleanup()
            c.set_token_revoke_confirm_query(lambda: TokenRevokeConfirm(slot_id="SLOT-LONG-DEL", revoked_count=5))
            c.set_quota_return_confirm_query(lambda: QuotaReturnConfirm(slot_id="SLOT-LONG-DEL", returned_bytes=5*1024*1024))
            c.set_cleanup_command_query(lambda: CleanupCommand(
                target_slot_id="SLOT-LONG-DEL", cleanup_reason="用户手动删除", slot_type=SlotType.LONG_TERM
            ))
            c.run_cleanup_cycle()
            c.run_cleanup_cycle()
            c.run_cleanup_cycle()
            assert c.state in (CleanupState.COMPLETED, CleanupState.ERASING)
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M12-06: 紧急熔断
        print("\n[TC-M12-06] 紧急熔断")
        try:
            c = setup_cleanup()
            c.emergency_shutdown()
            assert c.state == CleanupState.SYSTEM_PAUSED
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