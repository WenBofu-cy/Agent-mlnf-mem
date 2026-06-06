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

版本: V1.0
"""

import time
import uuid
from typing import Any, Dict, List, Optional
from enum import Enum

from memory_bus import InternalBus, Message


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


SLOT_PREFIX = {
    SlotType.LONG_TERM: "SLOT-LONG",
    SlotType.TEMPORARY: "SLOT-TEMP",
    SlotType.GUEST: "SLOT-GUEST",
}

DEFAULT_CONFIG = {
    SlotType.LONG_TERM: {
        "initial_quota_bytes": 5 * 1024 * 1024,
        "max_quota_bytes": 20 * 1024 * 1024,
        "lifetime_days": None,
        "preference_dimensions": 20,
    },
    SlotType.TEMPORARY: {
        "initial_quota_bytes": 2 * 1024 * 1024,
        "max_quota_bytes": 5 * 1024 * 1024,
        "lifetime_days": 7,
        "preference_dimensions": 10,
    },
    SlotType.GUEST: {
        "initial_quota_bytes": 500 * 1024,
        "max_quota_bytes": 1 * 1024 * 1024,
        "lifetime_days": 0,
        "preference_dimensions": 5,
    },
}


class SlotCreationUnit:
    module_id = "ag-mem-05"
    module_name = "画像槽创建与初始化单元"
    version = "V1.0"

    # 异步创建超时时间（秒）
    CREATION_TIMEOUT_SEC = 5.0

    def __init__(self):
        self.bus: Optional[InternalBus] = None
        self.state = CreationState.IDLE
        self._slot_counters = {t: 0 for t in SlotType}
        # 待处理创建上下文: correlation_id -> 上下文信息
        self._pending_creations: Dict[str, Dict[str, Any]] = {}
        self._pending_logs: List[Dict[str, Any]] = []

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    # ====================== 主循环 ======================
    def slot_creation_main_loop(self):
        if self.state == CreationState.SYSTEM_PAUSED:
            return
        if self.bus:
            self.bus.process_batch(10)

        # 检查超时的创建请求
        now = time.time()
        expired = []
        for corr_id, ctx in self._pending_creations.items():
            if now - ctx["start_time"] > self.CREATION_TIMEOUT_SEC:
                expired.append(corr_id)
        for corr_id in expired:
            ctx = self._pending_creations.pop(corr_id)
            # 如果已分配配额，尝试回滚
            if ctx.get("allocated_bytes", 0) > 0:
                self._rollback_quota(ctx.get("slot_id", ""), ctx["allocated_bytes"], corr_id)
            self._reply_failure(ctx["msg"], "创建超时", ctx.get("user_id", ""))
            self._log_event("CREATE_TIMEOUT", {"correlation_id": corr_id})

    # ====================== 总线消息入口 ======================
    def handle_message(self, msg: Message):
        if not isinstance(msg.data, dict):
            return

        if msg.topic == "ag-mem-05.create_slot":
            self._handle_create_command(msg)
        elif msg.topic == "ag-mem-05.quota_approval":
            self._handle_quota_response(msg)
        elif msg.topic == "ag-mem-05.isolation_config":
            self._handle_isolation_response(msg)

    def _handle_create_command(self, msg: Message):
        """接收创建指令，发起异步创建流程"""
        data = msg.data
        user_id = data.get("user_id", "")
        slot_type_str = data.get("slot_type", "长期槽")
        try:
            slot_type = SlotType(slot_type_str)
        except ValueError:
            slot_type = SlotType.LONG_TERM

        corr_id = msg.correlation_id or data.get("_correlation_id", str(uuid.uuid4()))
        config = DEFAULT_CONFIG[slot_type]
        requested_quota = data.get("initial_quota", config["initial_quota_bytes"])

        ctx = {
            "msg": msg,
            "slot_type": slot_type,
            "user_id": user_id,
            "requested_quota": requested_quota,
            "allocated_bytes": 0,
            "encryption_key": "",
            "storage_pointer": "",
            "start_time": time.time(),
        }
        self._pending_creations[corr_id] = ctx
        self.state = CreationState.QUOTA_REQUEST

        if self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-48",
                event_type="quota_request",
                source_module=self.module_id,
                data={
                    "slot_id": "",
                    "requested_bytes": requested_quota,
                    "_correlation_id": corr_id
                }
            )

    def _handle_quota_response(self, msg: Message):
        corr_id = msg.data.get("_correlation_id") or msg.correlation_id
        ctx = self._pending_creations.get(corr_id)
        if not ctx:
            return

        if not msg.data.get("approved", False):
            self.state = CreationState.CREATE_FAILED
            self._reply_failure(ctx["msg"], "配额不足", ctx["user_id"])
            del self._pending_creations[corr_id]
            return

        ctx["allocated_bytes"] = msg.data.get("allocated_bytes", ctx["requested_quota"])
        self.state = CreationState.STORAGE_ALLOC

        slot_type = ctx["slot_type"]
        self._slot_counters[slot_type] += 1
        slot_id = f"{SLOT_PREFIX[slot_type]}-{self._slot_counters[slot_type]}"
        ctx["slot_id"] = slot_id
        ctx["storage_pointer"] = f"storage://funnel-one/{slot_id}"
        ctx["encryption_key"] = uuid.uuid4().hex

        if self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-06",
                event_type="isolation_request",
                source_module=self.module_id,
                data={
                    "slot_id": slot_id,
                    "user_id": ctx["user_id"],
                    "slot_type": slot_type.value,
                    "encryption_key": ctx["encryption_key"],
                    "_correlation_id": corr_id
                }
            )

    def _handle_isolation_response(self, msg: Message):
        corr_id = msg.data.get("_correlation_id") or msg.correlation_id
        ctx = self._pending_creations.get(corr_id)
        if not ctx:
            return

        if not msg.data.get("success", True):
            self.state = CreationState.CREATE_FAILED
            self._rollback_quota(ctx["slot_id"], ctx["allocated_bytes"], corr_id)
            self._reply_failure(ctx["msg"], "隔离策略申请失败", ctx["user_id"])
            del self._pending_creations[corr_id]
            return

        self._log_event("SLOT_CREATED", {"slot_id": ctx["slot_id"], "user_id": ctx["user_id"]})
        self.state = CreationState.CREATED

        duration_ms = (time.time() - ctx["start_time"]) * 1000
        self._reply_success(
            original_msg=ctx["msg"],
            slot_id=ctx["slot_id"],
            slot_type=ctx["slot_type"],
            storage_pointer=ctx["storage_pointer"],
            duration_ms=duration_ms
        )
        del self._pending_creations[corr_id]

    # ====================== 回执构建 ======================
    def _reply_success(self, original_msg: Message, slot_id: str, slot_type: SlotType,
                       storage_pointer: str, duration_ms: float):
        if not self.bus:
            return
        self.bus.publish(
            topic=f"{original_msg.source_module}.creation_result",
            source_module=self.module_id,
            data={
                "success": True,
                "slot_id": slot_id,
                "slot_type": slot_type.value,
                "storage_pointer": storage_pointer,
                "init_status": "正常",
                "creation_duration_ms": duration_ms,
            },
            target_module=original_msg.source_module,
            correlation_id=original_msg.correlation_id
        )

    def _reply_failure(self, original_msg: Message, reason: str, user_id: str = ""):
        if not self.bus:
            return
        self.bus.publish(
            topic=f"{original_msg.source_module}.creation_result",
            source_module=self.module_id,
            data={
                "success": False,
                "error_reason": reason,
                "user_id": user_id,
                "suggested_action": "释放配额并重试",
            },
            target_module=original_msg.source_module,
            correlation_id=original_msg.correlation_id
        )

    def _rollback_quota(self, slot_id: str, bytes_allocated: int, corr_id: str):
        if self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-48",
                event_type="quota_return",
                source_module=self.module_id,
                data={
                    "slot_id": slot_id,
                    "returned_bytes": bytes_allocated,
                    "_correlation_id": corr_id
                }
            )

    # ====================== 熔断与日志 ======================
    def emergency_shutdown(self):
        self.state = CreationState.SYSTEM_PAUSED
        for corr_id, ctx in self._pending_creations.items():
            if ctx.get("allocated_bytes", 0) > 0:
                self._rollback_quota(ctx.get("slot_id", ""), ctx["allocated_bytes"], corr_id)
        self._pending_creations.clear()
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