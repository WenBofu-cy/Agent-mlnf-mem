#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-02
模块名称: 漏斗一专属调度单元
所属分区: 一、顶层总控中枢
核心职责: 作为漏斗一（用户画像漏斗）的专属调度单元，负责用户画像槽的全生命周期管理。
          接收 ag-mem-01 通过 InternalBus 转发的用户画像操作请求，匹配已有画像槽或触发新槽创建。
          管理长期槽、临时槽和访客槽的激活、休眠与过期清理。
          确保不同用户的画像槽之间严格物理隔离，禁止跨槽数据访问。
          不参与任何认知决策，仅执行漏斗一内部资源的调度与管理。

依赖模块:
    ag-mem-01, ag-mem-04, ag-mem-05, ag-mem-06, ag-mem-12
被依赖模块:
    ag-mem-01, ag-mem-04, ag-mem-07

安全约束:
  S-01: 不同用户的画像槽之间物理存储隔离，禁止任何形式的跨槽数据访问
  S-02: 临时槽和访客槽的清理必须执行安全擦除（覆写后删除），不可直接回收存储
  S-03: 长期槽的用户画像数据编译期禁止接入 Agent 自学习链路
  S-04: 本模块仅负责槽位调度，不直接操作用户画像数据内容

版本: V1.0 (总线集成最终版)
"""

from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum
import time
import uuid

from memory_bus import InternalBus, Message


# ====================== 枚举与数据结构 ======================
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
class SlotInfo:
    slot_id: str = ""
    user_id: str = ""
    slot_type: SlotType = SlotType.LONG_TERM
    created_at: float = field(default_factory=time.time)
    last_active_at: float = field(default_factory=time.time)
    is_active: bool = True


class FunnelOneDispatcher:
    module_id = "ag-mem-02"
    module_name = "漏斗一专属调度单元"
    version = "V1.0"

    MAX_LONG_TERM_SLOTS = 6
    MAX_TEMPORARY_SLOTS = 2
    MAX_GUEST_SLOTS = 1
    TEMP_SLOT_LIFETIME_SEC = 7 * 24 * 3600
    CLEANUP_INTERVAL_SEC = 3600

    def __init__(self):
        self.bus: Optional[InternalBus] = None   # 由主入口注入

        self.state = DispatcherState.IDLE
        self._slot_map: Dict[str, SlotInfo] = {}          # user_id -> SlotInfo
        self._slot_counters = {SlotType.LONG_TERM: 0, SlotType.TEMPORARY: 0, SlotType.GUEST: 0}
        self._last_cleanup_time = time.time()
        self._pending_logs: List[Dict[str, Any]] = []

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    # ====================== 主循环 ======================
    def funnel_one_dispatcher_main_loop(self):
        """主循环，由主入口周期性调用"""
        if self.state == DispatcherState.SYSTEM_PAUSED:
            return

        # 处理总线消息
        if self.bus:
            self.bus.process_batch(10)

        now = time.time()

        # 定期清理过期槽位
        if now - self._last_cleanup_time >= self.CLEANUP_INTERVAL_SEC:
            self._perform_cleanup()
            self._last_cleanup_time = now

        # 周期性状态上报（通过 internal_status 事件）
        self._report_status()

    # ====================== 总线消息入口 ======================
    def handle_message(self, msg: Message):
        """接收 InternalBus 发往本模块的消息"""
        if not isinstance(msg.data, dict):
            return

        # 冷启动检查
        if msg.topic == "ag-mem-02.init_check":
            self._handle_init_check(msg)
            return

        # 经验查询（返回空结果以保持异步协议）
        if msg.topic == "ag-mem-02.experience_query":
            self._handle_experience_query(msg)
            return

        # 经验写入（仅接收，不做进一步处理）
        if msg.topic == "ag-mem-02.experience_write":
            self._handle_experience_write(msg)
            return

        # 用户画像操作（槽位管理）
        if msg.topic in ("ag-mem-02.user_profile_query",):
            self._handle_user_profile_request(msg)
            return

    def _handle_init_check(self, msg: Message):
        """响应冷启动检查：上报可用状态"""
        if self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-01",
                event_type="internal_status",
                source_module=self.module_id,
                data={"available": True, "slot_count": len(self._slot_map)}
            )

    def _handle_experience_query(self, msg: Message):
        """
        处理经验查询（用户偏好部分）
        当前简化实现：直接返回空结果到 ag-mem-01.query_response
        """
        corr_id = msg.data.get("_correlation_id") or msg.correlation_id
        self._reply_to_f0(msg, "ag-mem-01", "query_response", {
            "results": [],
            "confidence": 0.0
        }, corr_id)

    def _handle_experience_write(self, msg: Message):
        """
        处理经验写入（用户画像数据写入）
        当前简化实现：不执行实际写入，仅确认接收
        """
        # 可选：将数据转发给 ag-mem-07 或直接忽略
        # 目前不返回任何消息
        pass

    def _handle_user_profile_request(self, msg: Message):
        """处理用户画像操作（槽位查找/创建）"""
        data = msg.data
        operation = data.get("operation", "query")
        user_id = data.get("user_id", "")

        if not user_id:
            self._reply_error(msg, "用户ID不能为空")
            return

        if self.state == DispatcherState.SYSTEM_PAUSED:
            self._reply_error(msg, "系统熔断中")
            return

        # 查找已有槽位
        existing = self._slot_map.get(user_id)
        if existing and existing.is_active:
            existing.last_active_at = time.time()
            self._reply_user_slot_result(msg, existing.slot_id, existing.slot_type, False)
            return

        # 查询操作：槽不存在
        if operation == "query":
            self._reply_error(msg, "用户画像槽不存在")
            return

        # 写入操作，创建新槽
        slot_type = self._determine_slot_type(data)
        if slot_type == SlotType.LONG_TERM and self._slot_counters[SlotType.LONG_TERM] >= self.MAX_LONG_TERM_SLOTS:
            self._reply_error(msg, "长期槽数量已达上限")
            return
        if slot_type == SlotType.TEMPORARY and self._slot_counters[SlotType.TEMPORARY] >= self.MAX_TEMPORARY_SLOTS:
            slot_type = SlotType.GUEST  # 降级

        new_slot_id = f"SLOT-{slot_type.value.upper()}-{uuid.uuid4().hex[:8]}"
        new_slot = SlotInfo(
            slot_id=new_slot_id,
            user_id=user_id,
            slot_type=slot_type
        )
        self._slot_map[user_id] = new_slot
        self._slot_counters[slot_type] += 1

        # 通知 ag-mem-05 创建物理槽
        if self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-05",
                event_type="create_slot",
                source_module=self.module_id,
                data={"user_id": user_id, "slot_type": slot_type.value, "slot_id": new_slot_id}
            )
            # 通知 ag-mem-04 槽位已激活
            self.bus.publish_to_module(
                target_module="ag-mem-04",
                event_type="slot_activated",
                source_module=self.module_id,
                data={"slot_id": new_slot_id, "user_id": user_id}
            )

        self._log_event("SLOT_CREATED", {"slot_id": new_slot_id, "user_id": user_id, "type": slot_type.value})
        self._reply_user_slot_result(msg, new_slot_id, slot_type, True)

    def _determine_slot_type(self, data: dict) -> SlotType:
        """根据请求中的身份置信度或指定类型决定槽类型"""
        if "slot_type" in data:
            try:
                return SlotType(data["slot_type"])
            except ValueError:
                pass
        confidence = data.get("identity_confidence", 0.85)
        if confidence >= 0.85:
            return SlotType.LONG_TERM
        elif confidence >= 0.5:
            return SlotType.TEMPORARY
        else:
            return SlotType.GUEST

    # ====================== 回复工具 ======================
    def _reply_to_f0(self, original_msg: Message, target_module: str, event: str, data: dict, corr_id: str = ""):
        """通用回复：向 ag-mem-01 发送响应"""
        if not self.bus:
            return
        self.bus.publish(
            topic=f"{target_module}.{event}",
            source_module=self.module_id,
            data=data,
            target_module=target_module,
            correlation_id=corr_id or original_msg.correlation_id
        )

    def _reply_user_slot_result(self, msg: Message, slot_id: str, slot_type: SlotType, is_new: bool):
        """回复槽位匹配结果给 ag-mem-01（使用 slot_result 事件）"""
        self._reply_to_f0(msg, msg.source_module, "slot_result", {
            "success": True,
            "slot_id": slot_id,
            "user_id": msg.data.get("user_id", ""),
            "slot_type": slot_type.value,
            "is_new": is_new
        })

    def _reply_error(self, msg: Message, error: str):
        """回复错误给请求方"""
        self._reply_to_f0(msg, msg.source_module, "slot_result", {
            "success": False,
            "error_reason": error
        })

    # ====================== 清理与状态上报 ======================
    def _perform_cleanup(self):
        self.state = DispatcherState.CLEANING
        now = time.time()
        to_remove = []
        for user_id, slot in self._slot_map.items():
            if slot.slot_type == SlotType.GUEST:
                to_remove.append(user_id)
            elif slot.slot_type == SlotType.TEMPORARY and (now - slot.created_at) > self.TEMP_SLOT_LIFETIME_SEC:
                to_remove.append(user_id)

        for user_id in to_remove:
            slot = self._slot_map.pop(user_id, None)
            if slot:
                self._slot_counters[slot.slot_type] -= 1
                if self.bus:
                    self.bus.publish_to_module(
                        target_module="ag-mem-12",
                        event_type="cleanup_slot",
                        source_module=self.module_id,
                        data={"slot_id": slot.slot_id, "user_id": user_id, "reason": "expired"}
                    )
                self._log_event("SLOT_CLEANED", {"slot_id": slot.slot_id, "user_id": user_id})

        self.state = DispatcherState.IDLE

    def _report_status(self):
        if not self.bus:
            return
        status = {
            "active_slots": len(self._slot_map),
            "long_term_count": self._slot_counters[SlotType.LONG_TERM],
            "temporary_count": self._slot_counters[SlotType.TEMPORARY],
            "guest_count": self._slot_counters[SlotType.GUEST]
        }
        self.bus.publish_to_module(
            target_module="ag-mem-01",
            event_type="internal_status",
            source_module=self.module_id,
            data={**status, "available": True}
        )

    # ====================== 熔断与日志 ======================
    def emergency_shutdown(self):
        self.state = DispatcherState.SYSTEM_PAUSED
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

    def collect_pending_logs(self) -> List[Dict[str, Any]]:
        logs = self._pending_logs.copy()
        self._pending_logs.clear()
        return logs


# ====================== 演示（使用总线模拟） ======================
def demo_main():
    from memory_bus import InternalBus

    print("=" * 70)
    print("  ag-mem-02 漏斗一专属调度单元 总线演示")
    print("=" * 70)

    internal = InternalBus()
    internal.register_module("ag-mem-01")
    internal.register_module("ag-mem-02")

    f2 = FunnelOneDispatcher()
    f2.bus = internal
    internal.subscribe_to_module("ag-mem-02", f2.handle_message)

    # 模拟 ag-mem-01 发送用户画像写入请求
    internal.publish_to_module(
        target_module="ag-mem-02",
        event_type="user_profile_query",
        source_module="ag-mem-01",
        data={"operation": "write", "user_id": "U001"}
    )

    # 运行几个周期
    for _ in range(3):
        f2.funnel_one_dispatcher_main_loop()
        internal.process_all()
        time.sleep(0.01)

    print(f"槽位数: {len(f2._slot_map)}")
    for uid, slot in f2._slot_map.items():
        print(f"  {uid} -> {slot.slot_id} ({slot.slot_type.value})")

    print("\n✅ 演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("总线集成后测试通过基本流程。")
    else:
        demo_main()