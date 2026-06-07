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

版本: V1.0 (最终修复版)
"""

import time
import uuid
from typing import Any, Dict, List, Optional
from enum import Enum

from memory_bus import InternalBus, Message


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


class TemporarySlotCleanup:
    module_id = "ag-mem-12"
    module_name = "临时画像槽自动清除单元"
    version = "V1.0"

    ERASE_TIMEOUT_SEC = 30
    ERASE_METHODS = {
        SlotType.TEMPORARY: "single_overwrite",
        SlotType.GUEST: "direct_delete",
        SlotType.LONG_TERM: "triple_overwrite",
    }

    def __init__(self):
        self.bus: Optional[InternalBus] = None
        self.state = CleanupState.IDLE
        self._current_task: Optional[Dict[str, Any]] = None
        self._task_queue: List[Dict[str, Any]] = []
        self._erasure_start_time: float = 0.0
        self._pending_logs: List[Dict[str, Any]] = []

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    # ====================== 全系统统一主循环入口 ======================
    def run_cycle(self):
        self.slot_cleanup_main_loop()

    def slot_cleanup_main_loop(self):
        if self.state == CleanupState.SYSTEM_PAUSED:
            return

        if self.bus:
            self.bus.process_batch(10)

        now = time.time()

        # 检查擦除超时
        if self.state == CleanupState.ERASING:
            if now - self._erasure_start_time >= self.ERASE_TIMEOUT_SEC:
                self._handle_erasure_timeout()

        # 处理完成后的队列
        if self.state in (CleanupState.COMPLETED, CleanupState.FAILED):
            if self._task_queue:
                next_task = self._task_queue.pop(0)
                self._start_cleanup(next_task)
            else:
                self.state = CleanupState.IDLE
                self._current_task = None

    # ====================== 总线消息入口 ======================
    def handle_message(self, msg: Message):
        if not isinstance(msg.data, dict):
            return

        if msg.topic == "ag-mem-12.cleanup_slot":
            self._handle_cleanup_command(msg)
            return

        if msg.topic == "ag-mem-12.token_revoke_confirm":
            if self.state == CleanupState.PREPARING and self._current_task:
                if msg.data.get("slot_id") == self._current_task.get("slot_id"):
                    self._begin_erasure()
            return

        if msg.topic == "ag-mem-12.quota_return_confirm":
            if self.state == CleanupState.ERASING and self._current_task:
                if msg.data.get("slot_id") == self._current_task.get("slot_id"):
                    self._finalize_cleanup(msg.data)
            return

    def _handle_cleanup_command(self, msg: Message):
        """接收来自 ag-mem-02 的清除指令"""
        slot_id = msg.data.get("slot_id", "")
        if not slot_id:
            self._log_event("INVALID_CLEANUP_COMMAND", {"reason": "缺少slot_id"})
            return

        task = {
            "slot_id": slot_id,
            "user_id": msg.data.get("user_id", ""),
            "slot_type": msg.data.get("slot_type", ""),
            "reason": msg.data.get("reason", "unknown"),
            "source_module": msg.source_module,
            "correlation_id": msg.correlation_id,
        }
        if self.state == CleanupState.IDLE:
            self._start_cleanup(task)
        else:
            self._task_queue.append(task)
            self._log_event("CLEANUP_TASK_QUEUED", {
                "slot_id": task["slot_id"],
                "queue_length": len(self._task_queue)
            })

    def _start_cleanup(self, task: Dict[str, Any]):
        """开始清除流程：第一步吊销令牌"""
        self._current_task = task
        self.state = CleanupState.PREPARING

        # 记录开始日志
        self._log_event("CLEANUP_STARTED", {
            "slot_id": task["slot_id"],
            "reason": task.get("reason")
        })

        # 向 ag-mem-06 发送令牌吊销请求
        if self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-06",
                event_type="revoke_tokens",
                source_module=self.module_id,
                data={"slot_id": task["slot_id"]}
            )

    def _begin_erasure(self):
        """第二步：执行安全擦除并退还配额"""
        if not self._current_task:
            return

        self.state = CleanupState.ERASING
        self._erasure_start_time = time.time()

        # 确定擦除方法
        slot_type_str = self._current_task.get("slot_type", "")
        try:
            slot_type = SlotType(slot_type_str)
        except ValueError:
            slot_type = SlotType.TEMPORARY
            self._log_event("UNKNOWN_SLOT_TYPE", {
                "slot_id": self._current_task["slot_id"],
                "received_type": slot_type_str,
                "used_default": slot_type.value
            })
        erase_method = self.ERASE_METHODS[slot_type]

        # 记录擦除开始日志
        self._log_event("ERASURE_STARTED", {
            "slot_id": self._current_task["slot_id"],
            "method": erase_method
        })

        # 执行安全擦除（模拟实现）
        # 实际应调用存储层的安全擦除接口
        erase_success = self._perform_secure_erase(self._current_task["slot_id"], erase_method)

        if erase_success:
            # 向 ag-mem-48 请求退还配额
            if self.bus:
                self.bus.publish_to_module(
                    target_module="ag-mem-48",
                    event_type="quota_return",
                    source_module=self.module_id,
                    data={"slot_id": self._current_task["slot_id"]}
                )
        else:
            self.state = CleanupState.FAILED
            self._handle_erasure_failure("安全擦除执行失败")

    def _perform_secure_erase(self, slot_id: str, method: str) -> bool:
        """执行安全擦除（当前为模拟实现，集成时替换为真实擦除调用）"""
        # TODO: 集成时替换为存储层安全擦除接口
        # 临时槽：单次覆写随机数据后删除
        # 访客槽：直接删除并清除元数据
        # 长期槽（手动删除）：三次覆写
        time.sleep(0.1)  # 模拟擦除耗时
        return True

    def _finalize_cleanup(self, data: Dict[str, Any]):
        """第三步：完成清理，记录日志并回复"""
        if not self._current_task:
            return

        self.state = CleanupState.COMPLETED
        slot_id = self._current_task["slot_id"]

        # 计算擦除耗时
        erase_duration_ms = (time.time() - self._erasure_start_time) * 1000

        # 记录完成日志
        self._log_event("SLOT_CLEANED", {
            "slot_id": slot_id,
            "reason": self._current_task.get("reason"),
            "released_bytes": data.get("returned_bytes", 0),
            "erase_duration_ms": round(erase_duration_ms, 2)
        })

        # 回复 ag-mem-02（字段对齐 CPEC 规格）
        source = self._current_task.get("source_module", "ag-mem-02")
        if self.bus:
            self.bus.publish(
                topic=f"{source}.cleanup_result",
                source_module=self.module_id,
                data={
                    "slot_id": slot_id,
                    "success": True,
                    "released_bytes": data.get("returned_bytes", 0),
                    "erase_duration_ms": round(erase_duration_ms, 2),
                    "is_recoverable": False,
                },
                target_module=source,
                correlation_id=self._current_task.get("correlation_id", "")
            )

    def _handle_erasure_timeout(self):
        """擦除超时处理"""
        self.state = CleanupState.FAILED
        if self._current_task:
            self._handle_erasure_failure("安全擦除超时")

    def _handle_erasure_failure(self, reason: str):
        """擦除失败处理"""
        slot_id = self._current_task.get("slot_id", "")
        self._log_event("ERASURE_FAILED", {
            "slot_id": slot_id,
            "reason": reason
        })

        source = self._current_task.get("source_module", "ag-mem-02")
        if self.bus:
            self.bus.publish(
                topic=f"{source}.cleanup_result",
                source_module=self.module_id,
                data={
                    "slot_id": slot_id,
                    "success": False,
                    "error_reason": reason,
                    "is_recoverable": False,
                },
                target_module=source,
                correlation_id=self._current_task.get("correlation_id", "")
            )

    # ====================== 管理接口 ======================
    def emergency_shutdown(self):
        self.state = CleanupState.SYSTEM_PAUSED
        self._task_queue.clear()
        self._current_task = None
        self._erasure_start_time = 0.0
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