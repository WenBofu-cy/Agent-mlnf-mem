#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-42
模块名称: 冗余记忆删除与归档单元
所属分区: 三、漏斗二：任务经验漏斗 / 晋升与遗忘执行机制
核心职责: 作为漏斗二中唯一有权物理清除数据的模块，接收来自 ag-mem-40 和 ag-mem-25 的
          条目清除指令，对指定的经验条目执行安全删除或冷归档。根据条目的当前层级与建议
          处理方式，L1/L2层级条目直接安全擦除，L3及以上层级条目压缩归档至冷存储以保留
          可追溯性。在完成删除或归档后，向 ag-mem-48 退还已释放的存储配额，并向 ag-mem-51
          记录完整的删除/归档事件。不参与遗忘判定或存储管理决策，仅执行已批准的条目清理操作。

依赖模块:
    ag-mem-40(遗忘阈值判定单元), ag-mem-25(L3相似经验归并单元),
    ag-mem-48(全局容量配额管控单元), ag-mem-49(存储压缩与冷归档单元),
    ag-mem-51(记忆变更日志追溯单元)
被依赖模块:
    ag-mem-40, ag-mem-25, ag-mem-20~26(各层级存储单元)

安全约束:
  D-01: L5核心层条目编译期拒绝任何删除或归档请求
  D-02: L3/L4层级条目必须先完整归档至冷存储，确认归档成功后，方可从源层级删除
  D-03: 本模块为漏斗二中唯一有权物理清除数据的模块
  D-04: 所有删除和归档操作必须记录完整的不可变事件日志
  D-05: 配额退还必须在删除或归档操作确认完成后执行

版本: V1.1 (生产缺陷修复版)
"""

import time
import uuid
from typing import Any, Dict, List, Optional
from enum import Enum

from memory_bus import InternalBus, Message


class PrunerState(Enum):
    IDLE = "idle"
    DELETING = "deleting"
    ARCHIVING = "archiving"
    QUOTA_RETURN = "quota_return"
    OPERATION_FAILED = "operation_failed"
    SYSTEM_PAUSED = "system_paused"


class RedundantMemoryPruner:
    module_id = "ag-mem-42"
    module_name = "冗余记忆删除与归档单元"
    version = "V1.1"

    # 系统常量配置
    MAX_BATCH_SIZE = 200
    BATCH_INTERVAL_MS = 100
    SINGLE_OP_TIMEOUT_SEC = 5
    # 新增：异步校验超时（修复缺陷1）
    ASYNC_CHECK_TIMEOUT_SEC = 10
    DEFAULT_LAYER = "L1"
    DEFAULT_SLOT_ID = "ag-mem-19"

    def __init__(self):
        self.bus: Optional[InternalBus] = None
        self.state = PrunerState.IDLE
        self._cleanup_queue: List[Dict[str, Any]] = []
        self._pending_logs: List[Dict[str, Any]] = []

        self._batch_context: Optional[Dict[str, Any]] = None
        self._pending_async_ops: int = 0

    # ====================== 统一调度入口 ======================
    def run_cycle(self):
        self.redundant_memory_pruner_main_loop()

    # ====================== 主循环（新增超时检测） ======================
    def redundant_memory_pruner_main_loop(self):
        if self.state == PrunerState.SYSTEM_PAUSED:
            return

        if self.bus:
            self.bus.process_batch(10)

        # 修复缺陷1：异步操作超时检测
        self._check_async_timeout()

        if self._cleanup_queue and self.state == PrunerState.IDLE:
            next_req = self._cleanup_queue.pop(0)
            self._process_cleanup(next_req)

    # ====================== 消息处理 ======================
    def handle_message(self, msg: Message):
        if self.state == PrunerState.SYSTEM_PAUSED:
            return
        if not isinstance(msg.data, dict):
            self._log_event("INVALID_MSG_FORMAT", {"topic": msg.topic, "reason": "数据非字典类型"})
            return

        try:
            if msg.topic == "ag-mem-42.forget_candidates":
                self._handle_cleanup_request(msg)
            elif msg.topic == "ag-mem-42.clear_entries":
                self._handle_cleanup_request(msg)
            elif msg.topic == "ag-mem-42.delete_confirm":
                self._handle_delete_confirm(msg)
            elif msg.topic == "ag-mem-42.archive_confirm":
                self._handle_archive_confirm(msg)
            elif msg.topic == "ag-mem-42.reuse_check_result":
                self._handle_reuse_check_result(msg)
        except Exception as e:
            self._log_event("MSG_PROCESS_ERROR", {"topic": msg.topic, "error": str(e)})

    def _handle_cleanup_request(self, msg: Message):
        request = {
            "candidates": msg.data.get("entries", []),
            "source": msg.source_module,
            "trigger_reason": msg.data.get("trigger_reason", ""),
            "correlation_id": msg.correlation_id
        }
        if self.state == PrunerState.IDLE:
            self._process_cleanup(request)
        else:
            self._cleanup_queue.append(request)

    def _process_cleanup(self, request: Dict[str, Any]):
        start_time = time.time()
        self.state = PrunerState.DELETING

        candidates = request.get("candidates", [])
        # 启用批量大小限制
        candidates = candidates[:self.MAX_BATCH_SIZE]

        self._batch_context = {
            "candidates": candidates,
            "deleted": 0,
            "archived": 0,
            "failed": [],
            "released": 0,
            "start_time": start_time,
            "source": request["source"],
            "correlation_id": request.get("correlation_id", "")
        }
        self._pending_async_ops = 0

        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            layer = candidate.get("source_layer", self.DEFAULT_LAYER)
            entry_id = candidate.get("entry_id", f"unknown_{uuid.uuid4().hex[:4]}")
            
            # D-01：L5拦截 + 新增日志
            if layer == "L5":
                self._batch_context["failed"].append({"entry_id": entry_id, "reason": "L5永久记忆禁止删除"})
                self._log_event("L5_PROTECTED", {"entry_id": entry_id})
                continue

            # 复用校验
            if candidate.get("pending_reuse_check"):
                self._pending_async_ops += 1
                if self.bus:
                    self.bus.publish_to_module(
                        target_module="ag-mem-41",
                        event_type="reuse_check_request",
                        source_module=self.module_id,
                        data={
                            "entry_id": entry_id,
                            "current_i": self._safe_float(candidate.get("i_value")),
                            "current_layer": layer,
                            "source_slot_id": candidate.get("source_slot_id", self.DEFAULT_SLOT_ID)
                        }
                    )
                continue

            # 执行操作
            if layer in ("L1", "L2"):
                self._send_delete_command(entry_id, layer)
            else:
                self._send_archive_command(candidate)

        if not self._pending_async_ops:
            self._finalize_batch()

    # ====================== 修复缺陷1：异步超时检测 ======================
    def _check_async_timeout(self):
        if not self._batch_context or self._pending_async_ops == 0:
            return
        elapsed = time.time() - self._batch_context["start_time"]
        if elapsed > self.ASYNC_CHECK_TIMEOUT_SEC:
            # 超时处理：标记所有待校验条目为失败
            for c in self._batch_context["candidates"]:
                if c.get("pending_reuse_check") and c.get("entry_id") not in [f["entry_id"] for f in self._batch_context["failed"]]:
                    self._batch_context["failed"].append({"entry_id": c["entry_id"], "reason": "复用校验超时"})
            self._pending_async_ops = 0
            self._try_finalize()

    def _send_delete_command(self, entry_id: str, layer: str):
        target_module = self._layer_to_module(layer)
        if self.bus:
            self.bus.publish_to_module(
                target_module=target_module,
                event_type="delete_entry",
                source_module=self.module_id,
                data={"entry_id": entry_id}
            )

    def _send_archive_command(self, candidate: Dict[str, Any]):
        if self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-49",
                event_type="archive_entry",
                source_module=self.module_id,
                data={"entry": candidate}
            )

    # ====================== 修复缺陷2：归档删除统计配额 ======================
    def _handle_delete_confirm(self, msg: Message):
        entry_id = msg.data.get("entry_id", "")
        success = msg.data.get("success", False)
        released = self._safe_int(msg.data.get("released_bytes", 0))

        if not self._batch_context:
            return
            
        if success:
            self._batch_context["deleted"] += 1
            self._batch_context["released"] += released
            self._log_cleanup(entry_id, "删除")
        else:
            self._batch_context["failed"].append({"entry_id": entry_id, "reason": msg.data.get("error", "删除失败")})
        self._try_finalize()

    def _handle_archive_confirm(self, msg: Message):
        entry_id = msg.data.get("entry_id", "")
        success = msg.data.get("success", False)

        if not self._batch_context:
            return
            
        if success:
            candidate = self._find_candidate(entry_id)
            if candidate:
                layer = candidate.get("source_layer", self.DEFAULT_LAYER)
                self._send_delete_command(entry_id, layer)
                self._batch_context["archived"] += 1
                self._log_cleanup(entry_id, "归档")
        else:
            self._batch_context["failed"].append({"entry_id": entry_id, "reason": "归档失败"})
        self._try_finalize()

    def _handle_reuse_check_result(self, msg: Message):
        entry_id = msg.data.get("entry_id", "")
        protected = msg.data.get("is_protected", False)
        
        self._pending_async_ops = max(0, self._pending_async_ops - 1)

        if protected:
            if self._batch_context:
                self._batch_context["failed"].append({"entry_id": entry_id, "reason": "复用保护"})
        else:
            candidate = self._find_candidate(entry_id)
            if candidate:
                layer = candidate.get("source_layer", self.DEFAULT_LAYER)
                if layer in ("L1", "L2"):
                    self._send_delete_command(entry_id, layer)
                else:
                    self._send_archive_command(candidate)
        
        self._try_finalize()

    # ====================== 修复缺陷3：增强候选查找 ======================
    def _find_candidate(self, entry_id: str) -> Optional[Dict]:
        if not self._batch_context or not entry_id:
            return None
        for c in self._batch_context["candidates"]:
            if c.get("entry_id") == entry_id:
                return c
        return None

    def _try_finalize(self):
        if self._pending_async_ops > 0:
            return
        self._finalize_batch()

    def _finalize_batch(self):
        ctx = self._batch_context
        if not ctx:
            return
            
        # D-05：退还配额
        total_released = ctx["released"]
        if total_released > 0 and self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-48",
                event_type="quota_return",
                source_module=self.module_id,
                data={"released_bytes": total_released}
            )

        # 回执上报
        if self.bus:
            self.bus.publish(
                topic=f"{ctx['source']}.cleanup_complete",
                source_module=self.module_id,
                data={
                    "total_entries": len(ctx["candidates"]),
                    "deleted_count": ctx["deleted"],
                    "archived_count": ctx["archived"],
                    "failed_entries": ctx["failed"],
                    "total_released_bytes": total_released,
                    "duration_ms": (time.time() - ctx["start_time"]) * 1000
                },
                target_module=ctx["source"],
                correlation_id=ctx["correlation_id"]
            )

        # 重置
        self._batch_context = None
        self._pending_async_ops = 0
        self.state = PrunerState.IDLE

    @staticmethod
    def _layer_to_module(layer: str) -> str:
        mapping = {
            "L1": "ag-mem-20", "L2": "ag-mem-22", "L3": "ag-mem-24",
            "L4": "ag-mem-26", "L5": "ag-mem-28"
        }
        return mapping.get(layer, "ag-mem-20")

    # ====================== 工具方法 ======================
    def _safe_float(self, value: Any) -> float:
        try:
            return float(value)
        except (ValueError, TypeError):
            return 0.0

    def _safe_int(self, value: Any) -> int:
        try:
            return int(value)
        except (ValueError, TypeError):
            return 0

    def _log_cleanup(self, entry_id: str, operation: str):
        self._log_event("MEMORY_CLEANUP", {"entry_id": entry_id, "operation": operation})

    # ====================== 管理接口 ======================
    def emergency_shutdown(self):
        self.state = PrunerState.SYSTEM_PAUSED
        self._cleanup_queue.clear()
        self._batch_context = None
        self._pending_async_ops = 0
        self._pending_logs.clear()
        self._log_event("EMERGENCY_SHUTDOWN", {"desc": "模块紧急停机"})

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