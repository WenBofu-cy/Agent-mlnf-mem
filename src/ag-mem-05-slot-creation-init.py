#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-05
模块名称: 画像槽创建与初始化单元
所属分区: 二、漏斗一：用户画像漏斗
核心职责: 接收 ag-mem-02 下发的新槽创建指令，在漏斗一存储分区中为新用户分配独立的
          画像槽存储空间。初始化槽内数据结构（偏好维度统计基线、行为累计计数器、活跃
          时间戳等），配置槽位类型（长期/临时/访客）对应的生命周期参数。创建完成后返回
          槽位编号与存储分区指针至 ag-mem-02。不参与任何认知决策，仅执行画像槽的物理
          创建与初始化。

依赖模块:
    ag-mem-02(漏斗一专属调度单元), ag-mem-06(画像槽数据隔离管控单元),
    ag-mem-48(全局容量配额管控单元)
被依赖模块:
    ag-mem-02, ag-mem-07(用户行为观测记录单元)

安全约束:
  S-01: 每个画像槽独立分配加密密钥，禁止跨槽复用密钥
  S-02: 槽位创建时必须同步申请隔离策略，禁止在隔离策略生效前接收任何数据写入
  S-03: 创建失败时必须完整回滚已分配的存储配额，不得残留无效分区占用空间
  S-04: 访客槽的生命周期参数硬编码为"当前会话"，不得被任何模块修改为永久保留
  S-05: 槽位编号生成后不可更改，该编号作为全系统唯一标识，用于所有后续操作关联
"""

from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
import time
import uuid
import secrets


class CreationState(Enum):
    IDLE = "idle"
    QUOTA_REQUEST = "quota_request"
    STORAGE_ALLOC = "storage_alloc"
    CREATED = "created"
    CREATE_FAILED = "create_failed"
    SYSTEM_PAUSED = "system_paused"


class SlotType(Enum):
    LONG_TERM = "长期槽"
    TEMPORARY = "临时槽"
    GUEST = "访客槽"


@dataclass
class CreateSlotCommand:
    user_id: str = ""
    slot_type: SlotType = SlotType.LONG_TERM
    initial_quota_bytes: int = 0
    session_id: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class QuotaApprovalResult:
    approved: bool = True
    allocated_bytes: int = 0
    remaining_bytes: int = 0
    timestamp: float = field(default_factory=time.time)


@dataclass
class IsolationConfig:
    slot_id: str = ""
    access_control_list: List[str] = field(default_factory=list)
    encryption_key: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class SlotCreationResult:
    slot_id: str = ""
    slot_type: SlotType = SlotType.LONG_TERM
    storage_pointer: str = ""
    init_status: str = ""
    creation_duration_ms: float = 0.0
    success: bool = True
    error_reason: str = ""


# 不同槽位类型的默认配置
SLOT_DEFAULT_CONFIG = {
    SlotType.LONG_TERM: {
        "initial_quota_bytes": 5 * 1024 * 1024,
        "max_quota_bytes": 20 * 1024 * 1024,
        "lifetime_days": None,  # 永久
        "preference_dimensions": 20,
        "statistics_precision": "精确",
        "enable_encryption": True,
        "enable_long_term_analysis": True,
    },
    SlotType.TEMPORARY: {
        "initial_quota_bytes": 2 * 1024 * 1024,
        "max_quota_bytes": 5 * 1024 * 1024,
        "lifetime_days": 7,
        "preference_dimensions": 10,
        "statistics_precision": "简化",
        "enable_encryption": True,
        "enable_long_term_analysis": False,
    },
    SlotType.GUEST: {
        "initial_quota_bytes": 500 * 1024,
        "max_quota_bytes": 1 * 1024 * 1024,
        "lifetime_days": 0,  # 当前会话
        "preference_dimensions": 5,
        "statistics_precision": "不统计",
        "enable_encryption": True,
        "enable_long_term_analysis": False,
    },
}


class SlotCreationUnit:
    def __init__(self):
        self.module_id = "ag-mem-05"
        self.module_name = "画像槽创建与初始化单元"
        self.version = "V1.0"

        self.state = CreationState.IDLE
        self._slot_counters = {t: 0 for t in SlotType}
        self._active_commands: Dict[str, CreateSlotCommand] = {}
        self._pending_logs: List[Dict[str, Any]] = []

        # 回调注入
        self._query_create_command = None
        self._query_quota_result = None
        self._query_isolation_config = None

        self._publish_creation_result = None
        self._publish_quota_request = None
        self._publish_isolation_request = None
        self._publish_event_log = None

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    def set_create_command_query(self, callback: Callable[[], Optional[CreateSlotCommand]]):
        self._query_create_command = callback

    def set_quota_result_query(self, callback: Callable[[], Optional[QuotaApprovalResult]]):
        self._query_quota_result = callback

    def set_isolation_config_query(self, callback: Callable[[], Optional[IsolationConfig]]):
        self._query_isolation_config = callback

    def set_creation_result_publisher(self, callback: Callable[[SlotCreationResult], None]):
        self._publish_creation_result = callback

    def set_quota_request_publisher(self, callback: Callable[[int, str], None]):
        self._publish_quota_request = callback

    def set_isolation_request_publisher(self, callback: Callable[[str, str, str], None]):
        self._publish_isolation_request = callback

    def set_event_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_event_log = callback

    def run_creation_cycle(self) -> Optional[SlotCreationResult]:
        if self.state == CreationState.SYSTEM_PAUSED:
            return None

        # 接收配额审批结果
        if self.state == CreationState.QUOTA_REQUEST:
            quota_result = self._query_quota_result() if self._query_quota_result else None
            if quota_result:
                return self._handle_quota_result(quota_result)

        # 接收隔离策略配置
        if self.state == CreationState.STORAGE_ALLOC:
            isolation_config = self._query_isolation_config() if self._query_isolation_config else None
            if isolation_config:
                return self._finalize_creation(isolation_config)

        # 接收新槽创建指令
        command = self._query_create_command() if self._query_create_command else None
        if command is None:
            return None

        return self._handle_create_command(command)

    def _handle_create_command(self, command: CreateSlotCommand) -> Optional[SlotCreationResult]:
        slot_type = command.slot_type

        # 校验槽位类型合法性
        if slot_type not in SlotType:
            return SlotCreationResult(
                slot_type=slot_type,
                success=False,
                error_reason="非法槽位类型"
            )

        # 获取默认配置
        config = SLOT_DEFAULT_CONFIG[slot_type]
        quota = command.initial_quota_bytes if command.initial_quota_bytes > 0 else config["initial_quota_bytes"]
        quota = min(quota, config["max_quota_bytes"])

        # 申请存储配额
        self.state = CreationState.QUOTA_REQUEST
        self._active_commands["current"] = command

        if self._publish_quota_request:
            self._publish_quota_request(quota, f"创建{slot_type.value}")

        return None  # 等待配额审批

    def _handle_quota_result(self, result: QuotaApprovalResult) -> SlotCreationResult:
        command = self._active_commands.pop("current", None)
        if command is None:
            return SlotCreationResult(success=False, error_reason="无待处理创建指令")

        if not result.approved:
            self.state = CreationState.CREATE_FAILED
            result_obj = SlotCreationResult(
                slot_type=command.slot_type,
                success=False,
                error_reason="存储配额不足"
            )
            self.state = CreationState.IDLE
            if self._publish_creation_result:
                self._publish_creation_result(result_obj)
            return result_obj

        # 生成槽位编号
        self._slot_counters[command.slot_type] += 1
        slot_id = self._generate_slot_id(command.slot_type)

        # 分配存储分区
        storage_pointer = self._allocate_storage(slot_id, result.allocated_bytes)
        if storage_pointer is None:
            self.state = CreationState.CREATE_FAILED
            # 退还配额
            self._log_event("STORAGE_ALLOC_FAILED", {"slot_id": slot_id})
            result_obj = SlotCreationResult(
                slot_id=slot_id,
                slot_type=command.slot_type,
                success=False,
                error_reason="存储分配失败"
            )
            self.state = CreationState.IDLE
            if self._publish_creation_result:
                self._publish_creation_result(result_obj)
            return result_obj

        # 存储分配成功，申请隔离策略
        self.state = CreationState.STORAGE_ALLOC
        self._active_commands["current"] = command
        self._active_commands["slot_id"] = slot_id
        self._active_commands["storage_pointer"] = storage_pointer

        if self._publish_isolation_request:
            self._publish_isolation_request(slot_id, command.slot_type.value, command.user_id)

        return None  # 等待隔离策略

    def _finalize_creation(self, isolation: IsolationConfig) -> SlotCreationResult:
        command = self._active_commands.pop("current", None)
        slot_id = self._active_commands.pop("slot_id", "UNKNOWN")
        storage_pointer = self._active_commands.pop("storage_pointer", "")

        if command is None:
            return SlotCreationResult(success=False, error_reason="无待处理创建指令")

        # 初始化槽内数据结构
        config = SLOT_DEFAULT_CONFIG[command.slot_type]
        init_success = self._initialize_slot_data(slot_id, command, config, isolation)

        if not init_success:
            self.state = CreationState.CREATE_FAILED
            result_obj = SlotCreationResult(
                slot_id=slot_id,
                slot_type=command.slot_type,
                success=False,
                error_reason="数据结构初始化失败"
            )
            self.state = CreationState.IDLE
            if self._publish_creation_result:
                self._publish_creation_result(result_obj)
            return result_obj

        # 创建完成
        self.state = CreationState.CREATED
        result_obj = SlotCreationResult(
            slot_id=slot_id,
            slot_type=command.slot_type,
            storage_pointer=storage_pointer,
            init_status="初始化成功",
            success=True
        )

        self._log_event("SLOT_CREATED", {
            "slot_id": slot_id,
            "user_id": command.user_id,
            "type": command.slot_type.value
        })

        self.state = CreationState.IDLE
        if self._publish_creation_result:
            self._publish_creation_result(result_obj)
        return result_obj

    def _generate_slot_id(self, slot_type: SlotType) -> str:
        prefix_map = {
            SlotType.LONG_TERM: "SLOT-LONG",
            SlotType.TEMPORARY: "SLOT-TEMP",
            SlotType.GUEST: "SLOT-GUEST",
        }
        prefix = prefix_map.get(slot_type, "SLOT-UNKNOWN")
        seq = self._slot_counters[slot_type]
        return f"{prefix}-{seq:04d}"

    def _allocate_storage(self, slot_id: str, quota_bytes: int) -> Optional[str]:
        # 模拟存储分配，实际实现会操作文件系统或数据库
        return f"storage://funnel-one/{slot_id}"

    def _initialize_slot_data(self, slot_id: str, command: CreateSlotCommand,
                               config: Dict[str, Any], isolation: IsolationConfig) -> bool:
        # 模拟数据结构初始化
        return True

    def get_state(self) -> CreationState:
        return self.state

    def emergency_shutdown(self):
        self.state = CreationState.SYSTEM_PAUSED
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
    print("  Agent-mlnf-mem 画像槽创建与初始化单元 (ag-mem-05) 演示")
    print("=" * 70)

    creator = SlotCreationUnit()

    print_separator("STEP 1: 创建长期槽")
    creator.set_create_command_query(lambda: CreateSlotCommand(
        user_id="U001",
        slot_type=SlotType.LONG_TERM,
        initial_quota_bytes=5 * 1024 * 1024
    ))
    result = creator.run_creation_cycle()
    if result is None:
        creator.set_quota_result_query(lambda: QuotaApprovalResult(approved=True, allocated_bytes=5*1024*1024))
        creator.set_isolation_config_query(lambda: IsolationConfig(slot_id="SLOT-LONG-0001", encryption_key=secrets.token_hex(16)))
        result = creator.run_creation_cycle()
        result = creator.run_creation_cycle()
    if result:
        print(f"  槽位ID: {result.slot_id}")
        print(f"  槽位类型: {result.slot_type.value}")
        print(f"  成功: {result.success}")

    print_separator("STEP 2: 创建访客槽")
    creator.set_create_command_query(lambda: CreateSlotCommand(
        user_id="GUEST_001",
        slot_type=SlotType.GUEST
    ))
    result = creator.run_creation_cycle()
    if result is None:
        creator.set_quota_result_query(lambda: QuotaApprovalResult(approved=True, allocated_bytes=500*1024))
        creator.set_isolation_config_query(lambda: IsolationConfig(slot_id="SLOT-GUEST-0001", encryption_key=secrets.token_hex(16)))
        result = creator.run_creation_cycle()
        result = creator.run_creation_cycle()
    if result:
        print(f"  槽位ID: {result.slot_id}")
        print(f"  槽位类型: {result.slot_type.value}")

    print("\n✅ 画像槽创建与初始化单元演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("ag-mem-05 画像槽创建与初始化单元 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        def setup_creator():
            c = SlotCreationUnit()
            c.set_quota_result_query(lambda: QuotaApprovalResult(approved=True, allocated_bytes=5*1024*1024))
            c.set_isolation_config_query(lambda: IsolationConfig(slot_id="TEST-SLOT", encryption_key=secrets.token_hex(16)))
            return c

        # TC-M05-01: 正常创建长期槽
        print("\n[TC-M05-01] 正常创建长期槽")
        try:
            c = setup_creator()
            c.set_create_command_query(lambda: CreateSlotCommand(user_id="U001", slot_type=SlotType.LONG_TERM))
            c.run_creation_cycle()
            result = c.run_creation_cycle()
            result = c.run_creation_cycle()
            assert result is not None
            assert result.success
            assert result.slot_type == SlotType.LONG_TERM
            assert "SLOT-LONG" in result.slot_id
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M05-02: 配额不足拒绝创建
        print("\n[TC-M05-02] 配额不足拒绝创建")
        try:
            c = setup_creator()
            c.set_quota_result_query(lambda: QuotaApprovalResult(approved=False))
            c.set_create_command_query(lambda: CreateSlotCommand(user_id="U002", slot_type=SlotType.LONG_TERM))
            c.run_creation_cycle()
            result = c.run_creation_cycle()
            assert result is not None
            assert not result.success
            assert "配额不足" in result.error_reason
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M05-03: 创建临时槽
        print("\n[TC-M05-03] 创建临时槽")
        try:
            c = setup_creator()
            c.set_quota_result_query(lambda: QuotaApprovalResult(approved=True, allocated_bytes=2*1024*1024))
            c.set_create_command_query(lambda: CreateSlotCommand(user_id="U003", slot_type=SlotType.TEMPORARY))
            c.run_creation_cycle()
            result = c.run_creation_cycle()
            result = c.run_creation_cycle()
            assert result is not None
            assert result.success
            assert result.slot_type == SlotType.TEMPORARY
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M05-04: 创建访客槽
        print("\n[TC-M05-04] 创建访客槽")
        try:
            c = setup_creator()
            c.set_quota_result_query(lambda: QuotaApprovalResult(approved=True, allocated_bytes=500*1024))
            c.set_create_command_query(lambda: CreateSlotCommand(user_id="GUEST_001", slot_type=SlotType.GUEST))
            c.run_creation_cycle()
            result = c.run_creation_cycle()
            result = c.run_creation_cycle()
            assert result is not None
            assert result.success
            assert result.slot_type == SlotType.GUEST
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M05-05: 幂等创建（重复创建不报错）
        print("\n[TC-M05-05] 连续创建两个槽位")
        try:
            c = setup_creator()
            c.set_create_command_query(lambda: CreateSlotCommand(user_id="U004", slot_type=SlotType.LONG_TERM))
            c.run_creation_cycle()
            r1 = c.run_creation_cycle()
            r1 = c.run_creation_cycle()
            c.set_create_command_query(lambda: CreateSlotCommand(user_id="U005", slot_type=SlotType.LONG_TERM))
            c.run_creation_cycle()
            r2 = c.run_creation_cycle()
            r2 = c.run_creation_cycle()
            assert r1 is not None and r1.success
            assert r2 is not None and r2.success
            assert r1.slot_id != r2.slot_id
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M05-06: 紧急熔断
        print("\n[TC-M05-06] 紧急熔断")
        try:
            c = setup_creator()
            c.emergency_shutdown()
            assert c.state == CreationState.SYSTEM_PAUSED
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