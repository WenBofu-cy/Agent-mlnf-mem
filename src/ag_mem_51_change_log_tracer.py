#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-51
模块名称: 记忆变更日志追溯单元
所属分区: 五、存储与系统运维
核心职责: 全链路记录双漏斗记忆中枢中所有记忆操作事件的不可变审计日志。涵盖经验写入、晋升、
          遗忘、归档、导入导出、安全仲裁、警示标签变更等全部记忆生命周期操作。每条日志包含
          UTC 时间戳、操作类型、操作模块、目标条目ID、变更前状态、变更后状态、操作原因摘要。
          日志存储采用追加写模式，禁止修改或删除已落盘的日志条目。存储周期 ≥ 3 年，为事故
          追溯、责任判定、合规审计提供完整的数据基础。支持按时间、操作类型、模块等多维度检索。
          不参与任何认知决策，仅负责日志的记录、存储与检索服务。

依赖模块:
    ag-mem-01 至 ag-mem-50（所有记忆模块均可向本模块推送变更日志事件）
被依赖模块:
    ag-mem-01, ECC-12, 系统管理接口, 离线复盘系统

安全约束:
  L-01: 日志存储必须采用追加写模式（Append-Only），禁止任何模块修改或删除已落盘的日志条目
  L-02: 每条日志条目必须包含全局单调递增序列号与 UTC 时间戳，确保时序不可篡改
  L-03: 事故相关的关键日志（安全仲裁、L5 锁定、配置变更）在存储空间不足时优先保留，不得被自动清理
  L-04: 日志存储周期 ≥ 3 年，超期日志在空间不足时优先清理，但需在清理前发送告警通知
  L-05: 日志 HMAC 签名用于内部完整性校验，发现签名不匹配的日志条目时应上报告警但不中断服务

版本: V1.0 (修复系统暂停消息丢失)
"""

import time
import uuid
import hmac
import hashlib
import json
from typing import Any, Dict, List, Optional
from enum import Enum

from memory_bus import InternalBus, Message


class TracerState(Enum):
    NORMAL_LOGGING = "normal_logging"
    LOW_STORAGE = "low_storage"
    STORAGE_FAULT = "storage_fault"
    SYSTEM_PAUSED = "system_paused"


class LogEventType(Enum):
    EXPERIENCE_WRITE = "经验写入"
    EXPERIENCE_PROMOTE = "经验晋升"
    EXPERIENCE_FORGET = "经验遗忘"
    EXPERIENCE_ARCHIVE = "经验归档"
    EXPERIENCE_RESTORE = "经验恢复"
    EXPERIENCE_MERGE = "经验归并"
    SLOT_CREATE = "画像槽创建"
    SLOT_DELETE = "画像槽清除"
    SAFETY_ARBITRATION = "安全仲裁"
    CAUTION_LABEL_CHANGE = "警示标签变更"
    L5_LOCK = "L5锁定/解锁"
    IMPORT = "经验导入"
    EXPORT = "经验导出"
    CAPACITY_CLEANUP = "容量清理"
    CONFIG_CHANGE = "配置变更"
    SYSTEM_EVENT = "系统事件"


class ChangeLogTracer:
    module_id = "ag-mem-51"
    module_name = "记忆变更日志追溯单元"
    version = "V1.0"

    # 存储配置
    TOTAL_CAPACITY_BYTES = 500 * 1024 * 1024     # 500MB
    RETENTION_YEARS = 3
    CRITICAL_EVENT_TYPES = {
        LogEventType.SAFETY_ARBITRATION,
        LogEventType.L5_LOCK,
        LogEventType.CONFIG_CHANGE,
    }
    LOW_STORAGE_THRESHOLD_PCT = 0.10
    CRITICAL_STORAGE_THRESHOLD_PCT = 0.05
    MAX_BUFFER_ENTRIES = 5000
    SIGNING_KEY = "change-log-tracer-secret"
    STATUS_REPORT_INTERVAL_SEC = 60
    ESTIMATED_ENTRY_BYTES = 256

    def __init__(self):
        self.bus: Optional[InternalBus] = None
        self.state = TracerState.NORMAL_LOGGING
        self._sequence_number: int = 0
        self._log_entries: List[Dict[str, Any]] = []
        self._total_bytes: int = 0
        self._consecutive_write_failures: int = 0
        self._buffer: List[Dict[str, Any]] = []
        self._last_status_time: float = time.time()
        self._pending_logs: List[Dict[str, Any]] = []

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成, "
              f"存储容量={self.TOTAL_CAPACITY_BYTES/1024/1024:.0f}MB")

    # ====================== 全系统统一调度入口 ======================
    def run_cycle(self):
        self.change_log_tracer_main_loop()

    # ====================== 主循环 ======================
    def change_log_tracer_main_loop(self):
        if self.state == TracerState.SYSTEM_PAUSED:
            # 暂停状态下仍然处理总线消息，只是不主动做容量上报等操作
            pass

        if self.bus:
            self.bus.process_batch(10)

        now = time.time()

        # 定期状态上报（暂停时跳过）
        if self.state != TracerState.SYSTEM_PAUSED and now - self._last_status_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._report_status()
            self._last_status_time = now

        # 存储故障恢复后自动回写缓冲区（暂停时也允许恢复）
        if self.state == TracerState.NORMAL_LOGGING and self._buffer:
            self._flush_buffer()

    # ====================== 总线消息入口 ======================
    def handle_message(self, msg: Message):
        if not isinstance(msg.data, dict):
            self._log_event("INVALID_MSG", {"reason": "日志数据格式非字典"})
            return

        try:
            if msg.topic == "ag-mem-51.log_event":
                # 日志记录始终接收，即使在暂停状态
                self._record_log(msg.data)
                return

            # 查询和导出在暂停时拒绝，返回提示
            if self.state == TracerState.SYSTEM_PAUSED:
                self._reply_service_unavailable(msg)
                return

            if msg.topic == "ag-mem-51.log_query":
                self._handle_query(msg)
                return

            if msg.topic == "ag-mem-51.log_export":
                self._handle_export(msg)
                return
        except Exception as e:
            self._log_event("MSG_PROCESS_ERROR", {"error": str(e)})

    def _reply_service_unavailable(self, msg: Message):
        """在暂停状态下回复查询/导出请求不可用"""
        if self.bus:
            target = msg.source_module
            if msg.topic == "ag-mem-51.log_query":
                self.bus.publish(
                    topic=f"{target}.log_query_result",
                    source_module=self.module_id,
                    data={
                        "matched_entries": [],
                        "total_matched": 0,
                        "error": "日志服务暂停中，查询不可用"
                    },
                    target_module=target,
                    correlation_id=msg.correlation_id
                )
            elif msg.topic == "ag-mem-51.log_export":
                self.bus.publish(
                    topic=f"{target}.log_export_result",
                    source_module=self.module_id,
                    data={
                        "export_time": time.time(),
                        "total_entries": 0,
                        "entries": [],
                        "error": "日志服务暂停中，导出不可用"
                    },
                    target_module=target,
                    correlation_id=msg.correlation_id
                )

    def _record_log(self, data: Dict[str, Any]):
        """记录一条日志条目"""
        self._sequence_number += 1
        now = time.time()

        entry = {
            "log_id": f"LOG-{self._sequence_number:08d}-{int(now)}",
            "sequence_number": self._sequence_number,
            "event_type": data.get("event_type", "SYSTEM_EVENT"),
            "source_module": data.get("source_module", ""),
            "target_entry_id": data.get("target_entry_id", ""),
            "previous_state": data.get("previous_state", ""),
            "new_state": data.get("new_state", ""),
            "reason_summary": data.get("reason_summary", ""),
            "related_data": data.get("related_data", {}),
            "timestamp": now,
            "signature": ""
        }

        # 计算 HMAC 签名 L-05
        entry["signature"] = self._compute_signature(entry)

        # 存储故障时暂存内存缓冲
        if self.state == TracerState.STORAGE_FAULT:
            self._buffer.append(entry)
            if len(self._buffer) >= self.MAX_BUFFER_ENTRIES:
                self._evict_oldest_non_critical()
            return

        # 系统暂停时也暂存到缓冲区，防止日志丢失
        if self.state == TracerState.SYSTEM_PAUSED:
            self._buffer.append(entry)
            if len(self._buffer) >= self.MAX_BUFFER_ENTRIES:
                self._evict_oldest_non_critical()
            return

        # 模拟持久化写入
        if self._simulate_write(entry):
            self._log_entries.append(entry)
            self._total_bytes += self.ESTIMATED_ENTRY_BYTES
            self._consecutive_write_failures = 0
            # 写入成功后尝试回写缓冲区
            if self._buffer:
                self._flush_buffer()
        else:
            self._consecutive_write_failures += 1
            if self._consecutive_write_failures >= 3:
                self.state = TracerState.STORAGE_FAULT
                self._send_storage_alert("存储故障", "连续写入失败3次，切换至内存缓冲模式")
                self._buffer.append(entry)

        # 容量检查 L-03 / L-04
        if self.state not in (TracerState.STORAGE_FAULT, TracerState.SYSTEM_PAUSED):
            remaining_pct = 1.0 - (self._total_bytes / self.TOTAL_CAPACITY_BYTES)
            if remaining_pct < self.CRITICAL_STORAGE_THRESHOLD_PCT:
                self.state = TracerState.LOW_STORAGE
                self._perform_cleanup(retention_years=2)
                self._send_storage_alert("空间严重不足", "已触发强制清理，保留最近2年数据")
            elif remaining_pct < self.LOW_STORAGE_THRESHOLD_PCT:
                self.state = TracerState.LOW_STORAGE
                self._perform_cleanup(retention_years=self.RETENTION_YEARS)
                self._send_storage_alert("空间不足", "已触发旧日志清理")

    def _flush_buffer(self):
        """将缓冲区中的日志逐条回写到主存储"""
        if not self._buffer:
            return
        flushed = 0
        for entry in self._buffer[:]:
            if self._simulate_write(entry):
                self._log_entries.append(entry)
                self._total_bytes += self.ESTIMATED_ENTRY_BYTES
                self._buffer.remove(entry)
                flushed += 1
            else:
                break
        if flushed > 0:
            self._log_event("BUFFER_FLUSHED", {"flushed_count": flushed, "remaining": len(self._buffer)})

    def _verify_signature(self, entry: Dict[str, Any]) -> bool:
        """验证单条日志的 HMAC 签名（L-05）"""
        stored_sig = entry.get("signature", "")
        computed_sig = self._compute_signature(entry)
        return hmac.compare_digest(stored_sig, computed_sig)

    def _compute_signature(self, entry: Dict[str, Any]) -> str:
        payload = f"{entry['sequence_number']}|{entry['event_type']}|{entry['source_module']}|{entry['target_entry_id']}|{entry['timestamp']}"
        return hmac.new(self.SIGNING_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]

    def _simulate_write(self, entry: Dict[str, Any]) -> bool:
        # 追加写模式 L-01，无修改/删除逻辑
        return True

    def _evict_oldest_non_critical(self):
        """内存缓冲满时，丢弃最旧的条目，但保留关键事件 L-03"""
        critical_values = [e.value for e in self.CRITICAL_EVENT_TYPES]
        for i, entry in enumerate(self._buffer):
            if entry.get("event_type", "") not in critical_values:
                self._buffer.pop(i)
                return
        if self._buffer:
            self._buffer.pop(0)

    def _handle_query(self, msg: Message):
        """处理日志查询请求，支持可选的签名验证"""
        data = msg.data
        start_time = time.time()
        matched = []
        verify_signatures = data.get("verify_signatures", False)
        tampered_count = 0

        time_range = data.get("time_range")
        module_id = data.get("module_id")
        event_type = data.get("event_type")
        target_entry_id = data.get("target_entry_id")
        keyword = data.get("keyword")
        max_results = data.get("max_results", 100)

        for entry in self._log_entries:
            if time_range and (time.time() - entry["timestamp"]) > time_range:
                continue
            if module_id and module_id != entry["source_module"]:
                continue
            if event_type and event_type != entry["event_type"]:
                continue
            if target_entry_id and target_entry_id != entry["target_entry_id"]:
                continue
            if keyword and keyword not in entry.get("reason_summary", ""):
                continue

            if verify_signatures and not self._verify_signature(entry):
                tampered_count += 1
                self._send_integrity_alert(entry)

            matched.append(entry)

        total = len(matched)
        matched.sort(key=lambda x: x["timestamp"], reverse=True)
        matched = matched[:max_results]
        elapsed = (time.time() - start_time) * 1000

        if self.bus:
            self.bus.publish(
                topic=f"{msg.source_module}.log_query_result",
                source_module=self.module_id,
                data={
                    "matched_entries": matched,
                    "total_matched": total,
                    "query_duration_ms": elapsed,
                    "is_complete": len(matched) == total,
                    "tampered_entries_detected": tampered_count if verify_signatures else -1
                },
                target_module=msg.source_module,
                correlation_id=msg.correlation_id
            )

    def _handle_export(self, msg: Message):
        """处理日志导出请求，导出前校验签名"""
        data = msg.data
        matched = []
        tampered_count = 0

        time_range = data.get("time_range")
        event_types = data.get("event_types", [])
        verify_signatures = data.get("verify_signatures", True)

        for entry in self._log_entries:
            if time_range and (time.time() - entry["timestamp"]) > time_range:
                continue
            if event_types and entry["event_type"] not in event_types:
                continue

            if verify_signatures and not self._verify_signature(entry):
                tampered_count += 1
                self._send_integrity_alert(entry)

            matched.append(entry)

        matched.sort(key=lambda x: x["timestamp"], reverse=True)

        package = {
            "export_time": time.time(),
            "total_entries": len(matched),
            "entries": matched,
            "tampered_entries_detected": tampered_count if verify_signatures else -1,
            "checksum": hashlib.sha256(json.dumps([e["log_id"] for e in matched]).encode()).hexdigest()
        }

        if self.bus:
            self.bus.publish(
                topic=f"{msg.source_module}.log_export_result",
                source_module=self.module_id,
                data=package,
                target_module=msg.source_module,
                correlation_id=msg.correlation_id
            )

    def _send_integrity_alert(self, entry: Dict[str, Any]):
        """发送签名不匹配告警（L-05）"""
        if self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-01",
                event_type="integrity_alert",
                source_module=self.module_id,
                data={
                    "alert_type": "日志签名校验失败",
                    "log_id": entry.get("log_id", ""),
                    "sequence_number": entry.get("sequence_number", 0),
                    "event_type": entry.get("event_type", ""),
                    "suggestion": "日志数据可能被篡改或损坏"
                }
            )

    # ====================== 容量管理 ======================
    def _perform_cleanup(self, retention_years: int = 3):
        retention_sec = retention_years * 365 * 86400
        cutoff = time.time() - retention_sec
        new_entries = []
        removed_bytes = 0
        critical_values = [e.value for e in self.CRITICAL_EVENT_TYPES]
        for entry in self._log_entries:
            # 关键日志永久保留 L-03
            if entry["timestamp"] < cutoff and entry["event_type"] not in critical_values:
                removed_bytes += self.ESTIMATED_ENTRY_BYTES
            else:
                new_entries.append(entry)
        self._log_entries = new_entries
        self._total_bytes -= removed_bytes

    def _send_storage_alert(self, alert_type: str, suggestion: str):
        if self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-01",
                event_type="storage_alert",
                source_module=self.module_id,
                data={
                    "alert_type": alert_type,
                    "current_status": self.state.value,
                    "suggested_action": suggestion
                }
            )

    def _report_status(self):
        if self.bus:
            oldest = min((e["timestamp"] for e in self._log_entries), default=0)
            newest = max((e["timestamp"] for e in self._log_entries), default=0)
            self.bus.publish_to_module(
                target_module="ag-mem-48",
                event_type="storage_status",
                source_module=self.module_id,
                data={
                    "total_capacity_bytes": self.TOTAL_CAPACITY_BYTES,
                    "used_bytes": self._total_bytes,
                    "remaining_bytes": self.TOTAL_CAPACITY_BYTES - self._total_bytes,
                    "oldest_log_time": oldest,
                    "newest_log_time": newest,
                    "write_rate_per_sec": 0.0
                }
            )

    # ====================== 管理接口 ======================
    def emergency_shutdown(self):
        self.state = TracerState.SYSTEM_PAUSED
        if self._buffer:
            for entry in self._buffer:
                self._log_entries.append(entry)
                self._total_bytes += self.ESTIMATED_ENTRY_BYTES
            self._buffer.clear()
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