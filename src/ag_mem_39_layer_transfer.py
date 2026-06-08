#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-39
模块名称: 层级单向搬运写入单元
所属分区: 三、漏斗二：任务经验漏斗 / 晋升与遗忘执行机制
核心职责: 接收 ag-mem-38 下发的晋升候选清单，执行经验条目从当前层级向上一层的物理搬运。
          确保搬运过程严格单向——经验只能从低层级向高层级晋升，禁止回退或越级。在搬运完成后，
          通知源层级存储删除原始条目，并向目标层级确认写入成功。搬运失败时保留原始条目不变并
          回滚已写入的部分。不参与晋升判定，仅执行已批准晋升条目的物理搬运。

依赖模块:
    ag-mem-38, ag-mem-20~28, ag-mem-29, ag-mem-48, ag-mem-51
被依赖模块:
    ag-mem-38, ag-mem-51

安全约束:
  T-01: 编译期硬编码合法晋升路径（L1→L2→L3→L4→L5），禁止回退或越级
  T-02: 严格遵循"先写目标，确认成功后再删源"的顺序，确保数据不丢失
  T-03: 目标层级写入失败时，已部分完成的操作必须完整回滚
  T-04: L4→L5晋升必须携带 ag-mem-29 签发的有效安全令牌，否则拒绝
  T-05: 每条搬运操作必须记录完整的晋升事件日志

版本: V1.0 (安全令牌验证增强版)
"""

import time
import uuid
from typing import Any, Dict, List, Optional
from enum import Enum

from memory_bus import InternalBus, Message


class TransferState(Enum):
    IDLE = "idle"
    CAPACITY_CHECK = "capacity_check"
    TOKEN_VALIDATION = "token_validation"
    TRANSFERRING = "transferring"
    ROLLING_BACK = "rolling_back"
    SYSTEM_PAUSED = "system_paused"


# 全局常量定义区
# 合法晋升路径 (T-01 硬编码约束)
LEGAL_TRANSFERS = {
    ("L1", "L2"), ("L2", "L3"), ("L3", "L4"), ("L4", "L5")
}
# 层级与存储模块映射
LAYER_MODULE_MAP = {
    "L1": "ag-mem-20",
    "L2": "ag-mem-22",
    "L3": "ag-mem-24",
    "L4": "ag-mem-26",
    "L5": "ag-mem-28",
}
# 超时配置
WRITE_TIMEOUT_SEC = 5.0
DELETE_TIMEOUT_SEC = 5.0
TOKEN_VALIDATION_TIMEOUT_SEC = 5.0
# 兜底默认值
DEFAULT_ENTRY_ID = "unknown"
DEFAULT_I_VALUE = 0.0


class LayerTransferUnit:
    module_id = "ag-mem-39"
    module_name = "层级单向搬运写入单元"
    version = "V1.0"

    def __init__(self):
        self.bus: Optional[InternalBus] = None
        self.state = TransferState.IDLE
        # 当前搬运批次上下文
        self._current_batch: Optional[Dict[str, Any]] = None
        self._written_entry_ids: List[str] = []
        # L4→L5 令牌验证挂起表
        self._pending_token_validations: Dict[str, Dict[str, Any]] = {}
        self._pending_logs: List[Dict[str, Any]] = []

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    # ====================== 全系统统一调度入口 ======================
    def run_cycle(self):
        self.layer_transfer_main_loop()

    # ====================== 主循环 ======================
    def layer_transfer_main_loop(self):
        if self.state == TransferState.SYSTEM_PAUSED:
            return

        if self.bus:
            self.bus.process_batch(10)

        # 搬运超时检测
        self._check_transfer_timeout()

        # 令牌验证超时检测
        self._check_token_validation_timeout()

    # ====================== 总线消息入口 ======================
    def handle_message(self, msg: Message):
        if self.state == TransferState.SYSTEM_PAUSED:
            return
        if not isinstance(msg.data, dict):
            self._log_event("INVALID_MSG_FORMAT", {"topic": msg.topic, "reason": "消息数据非字典类型"})
            return

        try:
            if msg.topic == "ag-mem-39.promotion_list":
                self._start_transfer(msg)
            elif msg.topic == "ag-mem-39.write_confirm":
                self._handle_write_confirm(msg)
            elif msg.topic == "ag-mem-39.delete_confirm":
                self._handle_delete_confirm(msg)
            elif msg.topic == "ag-mem-39.capacity_response":
                self._handle_capacity_response(msg)
            elif msg.topic == "ag-mem-39.token_validation_response":
                self._handle_token_validation_response(msg)
        except Exception as e:
            self._log_event("MSG_PROCESS_EXCEPTION", {
                "topic": msg.topic,
                "error_msg": str(e)
            })

    # ====================== 搬运核心流程 ======================
    def _start_transfer(self, msg: Message):
        """开始新的搬运批次"""
        if self.state != TransferState.IDLE:
            self._reply_transfer_complete(
                target=msg.source_module,
                failed=[],
                success=0,
                error="模块忙碌中，正在处理其他批次"
            )
            return

        data = msg.data
        source_layer = data.get("source_layer", "").strip()
        target_layer = data.get("target_layer", "").strip()
        entries = data.get("entries", [])

        # 校验晋升路径合法性 (T-01)
        if (source_layer, target_layer) not in LEGAL_TRANSFERS:
            self._reply_transfer_complete(
                target=msg.source_module,
                failed=[],
                success=0,
                error=f"非法晋升路径: {source_layer} → {target_layer}"
            )
            return

        if not entries:
            self._reply_transfer_complete(
                target=msg.source_module,
                failed=[],
                success=0,
                error="无待搬运条目"
            )
            return

        # L4→L5 强制校验安全令牌 (T-04)：向 ag-mem-29 发起异步验证
        if source_layer == "L4" and target_layer == "L5":
            self.state = TransferState.TOKEN_VALIDATION
            corr_id = msg.correlation_id or str(uuid.uuid4())

            # 初始化批次上下文（暂不开始搬运）
            self._current_batch = {
                "source_module": msg.source_module,
                "source_layer": source_layer,
                "target_layer": target_layer,
                "entries": entries,
                "current_index": 0,
                "success_count": 0,
                "failed_entries": [],
                "correlation_id": corr_id,
                "start_time": time.time()
            }

            # 收集所有条目的令牌
            token_ids = []
            for entry_data in entries:
                token = entry_data.get("security_token", {})
                token_id = token.get("token_id", "") if isinstance(token, dict) else ""
                if not token_id:
                    self._reply_transfer_complete(
                        target=msg.source_module,
                        failed=[],
                        success=0,
                        error="L4→L5 晋升条目缺少有效安全令牌"
                    )
                    self._current_batch = None
                    self.state = TransferState.IDLE
                    return
                token_ids.append(token_id)

            # 向 ag-mem-29 发起令牌验证请求
            if self.bus:
                self.bus.publish_to_module(
                    target_module="ag-mem-29",
                    event_type="validate_token",
                    source_module=self.module_id,
                    data={
                        "token_ids": token_ids,
                        "source_layer": source_layer,
                        "target_layer": target_layer,
                        "_correlation_id": corr_id
                    }
                )

            # 记录挂起的令牌验证
            self._pending_token_validations[corr_id] = {
                "batch": self._current_batch,
                "start_time": time.time()
            }
            return

        # 非 L4→L5 路径，直接进入容量检查
        self._initiate_capacity_check(msg)

    def _handle_token_validation_response(self, msg: Message):
        """处理 ag-mem-29 的令牌验证回执"""
        if self.state != TransferState.TOKEN_VALIDATION:
            return

        corr_id = msg.data.get("_correlation_id") or msg.correlation_id
        pending = self._pending_token_validations.pop(corr_id, None)
        if not pending:
            return

        if not msg.data.get("valid", False):
            # 令牌验证失败，整批拒绝
            batch = pending["batch"]
            failed_list = [
                {"entry_id": e.get("entry_id", DEFAULT_ENTRY_ID), "reason": "L5安全令牌验证失败"}
                for e in batch["entries"]
            ]
            self._reply_transfer_complete(
                target=batch["source_module"],
                failed=failed_list,
                success=0,
                error=msg.data.get("reason", "令牌验证失败")
            )
            self._current_batch = None
            self.state = TransferState.IDLE
            return

        # 令牌验证通过，进入容量检查
        self._current_batch = pending["batch"]
        self._initiate_capacity_check_internal()

    def _check_token_validation_timeout(self):
        """检查令牌验证是否超时"""
        if self.state != TransferState.TOKEN_VALIDATION:
            return
        now = time.time()
        expired = []
        for corr_id, pending in self._pending_token_validations.items():
            if now - pending["start_time"] > TOKEN_VALIDATION_TIMEOUT_SEC:
                expired.append(corr_id)
        for corr_id in expired:
            pending = self._pending_token_validations.pop(corr_id)
            batch = pending["batch"]
            failed_list = [
                {"entry_id": e.get("entry_id", DEFAULT_ENTRY_ID), "reason": "令牌验证超时"}
                for e in batch["entries"]
            ]
            self._reply_transfer_complete(
                target=batch["source_module"],
                failed=failed_list,
                success=0,
                error="令牌验证超时"
            )
            self._current_batch = None
            self.state = TransferState.IDLE

    def _initiate_capacity_check(self, msg: Message):
        """启动容量检查（外部消息入口）"""
        self._current_batch = {
            "source_module": msg.source_module,
            "source_layer": msg.data.get("source_layer", "").strip(),
            "target_layer": msg.data.get("target_layer", "").strip(),
            "entries": msg.data.get("entries", []),
            "current_index": 0,
            "success_count": 0,
            "failed_entries": [],
            "correlation_id": msg.correlation_id,
            "start_time": time.time()
        }
        self._initiate_capacity_check_internal()

    def _initiate_capacity_check_internal(self):
        """向 ag-mem-48 查询目标层级容量"""
        self.state = TransferState.CAPACITY_CHECK
        if self.bus and self._current_batch:
            self.bus.publish_to_module(
                target_module="ag-mem-48",
                event_type="capacity_query",
                source_module=self.module_id,
                data={
                    "target_layer": self._current_batch["target_layer"],
                    "required_entries": len(self._current_batch["entries"])
                }
            )

    def _handle_capacity_response(self, msg: Message):
        """处理容量查询回执"""
        if self.state != TransferState.CAPACITY_CHECK or not self._current_batch:
            return

        batch = self._current_batch
        if msg.data.get("available", True):
            # 容量充足，开始逐一条目搬运
            self.state = TransferState.TRANSFERRING
            self._written_entry_ids.clear()
            self._process_next_entry()
        else:
            # 目标层级容量不足，整批失败
            failed_list = [
                {"entry_id": e.get("entry_id", DEFAULT_ENTRY_ID), "reason": "目标层级容量不足"}
                for e in batch["entries"]
            ]
            self._reply_transfer_complete(
                target=batch["source_module"],
                failed=failed_list,
                success=0,
                error="目标层级容量不足"
            )
            self._current_batch = None
            self.state = TransferState.IDLE

    def _process_next_entry(self):
        """处理下一条待搬运条目"""
        if not self._current_batch:
            return

        batch = self._current_batch
        idx = batch["current_index"]
        entries = batch["entries"]

        if idx >= len(entries):
            # 本批次全部处理完成
            self._finalize_transfer()
            return

        entry_data = entries[idx]
        target_layer = batch["target_layer"]
        target_module = self._get_layer_module(target_layer)

        if self.bus:
            self.bus.publish_to_module(
                target_module=target_module,
                event_type="promoted_entries",
                source_module=self.module_id,
                data={"entries": [entry_data], "promotion_source": batch["source_layer"]}
            )

    def _handle_write_confirm(self, msg: Message):
        """处理目标层级写入回执"""
        if self.state != TransferState.TRANSFERRING or not self._current_batch:
            return

        batch = self._current_batch
        idx = batch["current_index"]
        entry_data = batch["entries"][idx]
        entry_id = entry_data.get("entry_id", DEFAULT_ENTRY_ID)

        if msg.data.get("success", False):
            # 写入成功：记录已写入条目，执行源层级删除 (T-02 先写后删)
            self._written_entry_ids.append(entry_id)
            source_module = self._get_layer_module(batch["source_layer"])
            if self.bus:
                self.bus.publish_to_module(
                    target_module=source_module,
                    event_type="delete_entries",
                    source_module=self.module_id,
                    data={"entry_ids": [entry_id], "reason": f"晋升至{batch['target_layer']}"}
                )
        else:
            # 写入失败：执行整体回滚 (T-03)
            self.state = TransferState.ROLLING_BACK
            error_reason = msg.data.get("error", "目标层级写入失败")
            self._rollback_written_entries()

            # 标记当前及剩余所有条目为失败
            batch["failed_entries"].append({"entry_id": entry_id, "reason": error_reason})
            remaining_entries = batch["entries"][idx + 1:]
            for e in remaining_entries:
                batch["failed_entries"].append({
                    "entry_id": e.get("entry_id", DEFAULT_ENTRY_ID),
                    "reason": "搬运流程中断，整体终止"
                })
            self._finalize_transfer()

    def _handle_delete_confirm(self, msg: Message):
        """处理源层级删除回执"""
        if self.state != TransferState.TRANSFERRING or not self._current_batch:
            return

        batch = self._current_batch
        idx = batch["current_index"]
        entry_data = batch["entries"][idx]
        entry_id = entry_data.get("entry_id", DEFAULT_ENTRY_ID)

        if msg.data.get("success", False):
            batch["success_count"] += 1
        else:
            batch["success_count"] += 1
            batch["failed_entries"].append({
                "entry_id": entry_id,
                "reason": "源层级删除失败，条目双向留存"
            })

        # 记录晋升日志 (T-05)
        self._log_event("PROMOTION_RECORD", {
            "entry_id": entry_id,
            "source_layer": batch["source_layer"],
            "target_layer": batch["target_layer"],
            "i_value": entry_data.get("i_value", DEFAULT_I_VALUE)
        })

        # 处理下一条
        batch["current_index"] += 1
        self._process_next_entry()

    def _rollback_written_entries(self):
        """回滚已写入目标层级的条目 (T-03)"""
        if not self._current_batch or not self._written_entry_ids:
            return

        batch = self._current_batch
        target_module = self._get_layer_module(batch["target_layer"])
        for eid in self._written_entry_ids:
            if self.bus:
                self.bus.publish_to_module(
                    target_module=target_module,
                    event_type="delete_entries",
                    source_module=self.module_id,
                    data={"entry_ids": [eid], "reason": "搬运失败，执行回滚"}
                )
        self._log_event("TRANSFER_ROLLBACK", {
            "rollback_entry_count": len(self._written_entry_ids),
            "source_layer": batch["source_layer"],
            "target_layer": batch["target_layer"]
        })
        self._written_entry_ids.clear()

    def _finalize_transfer(self):
        """批次搬运收尾"""
        if not self._current_batch:
            return

        batch = self._current_batch
        duration_ms = (time.time() - batch["start_time"]) * 1000
        self._reply_transfer_complete(
            target=batch["source_module"],
            failed=batch["failed_entries"],
            success=batch["success_count"],
            error="" if not batch["failed_entries"] else "批次部分条目搬运失败",
            duration_ms=duration_ms
        )

        self._current_batch = None
        self._written_entry_ids.clear()
        self.state = TransferState.IDLE

    # ====================== 工具方法 ======================
    def _get_layer_module(self, layer: str) -> str:
        return LAYER_MODULE_MAP.get(layer, "ag-mem-20")

    def _check_transfer_timeout(self):
        if not self._current_batch or self.state != TransferState.TRANSFERRING:
            return
        run_time = time.time() - self._current_batch["start_time"]
        if run_time > (WRITE_TIMEOUT_SEC + DELETE_TIMEOUT_SEC):
            self._log_event("TRANSFER_TIMEOUT", {
                "run_seconds": run_time,
                "source_layer": self._current_batch["source_layer"],
                "target_layer": self._current_batch["target_layer"]
            })

    def _reply_transfer_complete(self, target: str, failed: List[Dict], success: int,
                                  error: str = "", duration_ms: float = 0.0):
        if not self.bus:
            return

        total = 0
        if self._current_batch:
            total = len(self._current_batch["entries"])

        self.bus.publish(
            topic=f"{target}.transfer_complete",
            source_module=self.module_id,
            data={
                "source_layer": self._current_batch["source_layer"] if self._current_batch else "",
                "target_layer": self._current_batch["target_layer"] if self._current_batch else "",
                "total_entries": total,
                "success_count": success,
                "failed_entries": failed,
                "error": error,
                "duration_ms": duration_ms
            },
            target_module=target,
            correlation_id=self._current_batch.get("correlation_id", "") if self._current_batch else ""
        )

    # ====================== 管理接口 & 日志 ======================
    def emergency_shutdown(self):
        self.state = TransferState.SYSTEM_PAUSED
        self._current_batch = None
        self._written_entry_ids.clear()
        self._pending_token_validations.clear()
        self._pending_logs.clear()
        self._log_event("EMERGENCY_SHUTDOWN", {"desc": "模块触发紧急停机，清空所有临时数据"})

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
        logs = self._pending_logs.copy()
        self._pending_logs.clear()
        return logs