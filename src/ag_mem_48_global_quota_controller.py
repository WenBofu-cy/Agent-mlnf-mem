#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-48
模块名称: 全局容量配额管控单元
所属分区: 五、存储与系统运维
核心职责: 监控双漏斗记忆中枢的全局存储使用量，实施分层级的容量配额管控。
          当存储逼近预置上限时触发分级响应：80% 温和预警，90% 启动低重要度清理，
          95% 暂停非关键写入并强制清理。所有清理操作通过 ag-mem-42 安全执行。
          不参与经验内容的删除决策，仅负责容量监控与清理触发。

依赖模块: ag-mem-20~28, ag-mem-42
被依赖模块: ag-mem-01, ag-mem-03, ag-mem-05, ag-mem-20~28

安全约束:
  Q-01: 容量上限可在配置文件中扩展，但运行时不得低于最小安全容量
  Q-02: 清理操作必须通过 ag-mem-42 执行
  Q-03: L5 核心层条目永不参与容量管控驱动的清理
  Q-04: 容量紧急时仅暂停 L1/L2 写入，L3-L5 不受影响

版本: V1.0
"""

import time
import uuid
from typing import Any, Dict, List, Optional
from enum import Enum

from memory_bus import InternalBus, Message


class QuotaState(Enum):
    NORMAL = "normal"
    CAPACITY_WARNING = "capacity_warning"
    CAPACITY_CRITICAL = "capacity_critical"
    SYSTEM_PAUSED = "system_paused"


class GlobalQuotaController:
    module_id = "ag-mem-48"
    module_name = "全局容量配额管控单元"
    version = "V1.0"

    DEFAULT_TOTAL_CAPACITY_BYTES = 100 * 1024 * 1024   # 100MB
    MIN_SAFE_CAPACITY_BYTES = 10 * 1024 * 1024
    WARN_THRESHOLD = 0.80
    CRITICAL_THRESHOLD = 0.90
    TARGET_WARN_RELEASE_PCT = 0.75
    TARGET_CRITICAL_RELEASE_PCT = 0.85
    STATUS_REPORT_INTERVAL_SEC = 30

    def __init__(self):
        self.bus: Optional[InternalBus] = None
        self.state = QuotaState.NORMAL
        self._total_capacity_bytes = self.DEFAULT_TOTAL_CAPACITY_BYTES
        self._layer_usage: Dict[str, Dict[str, Any]] = {}
        self._total_usage_bytes: int = 0
        self._write_paused: bool = False
        self._last_status_time: float = time.time()
        self._pending_logs: List[Dict[str, Any]] = []

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成, "
              f"总容量={self._total_capacity_bytes/1024/1024:.0f}MB")

    # ====================== 主循环 ======================
    def global_quota_controller_main_loop(self):
        if self.state == QuotaState.SYSTEM_PAUSED:
            return

        if self.bus:
            self.bus.process_batch(10)

        now = time.time()
        if now - self._last_status_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._report_status()
            self._last_status_time = now

    # ====================== 总线消息入口 ======================
    def handle_message(self, msg: Message):
        if not isinstance(msg.data, dict):
            return

        # 各层级存储使用量上报
        if msg.topic == "ag-mem-48.storage_status":
            self._handle_storage_report(msg.data)
            return

        # 容量查询请求
        if msg.topic == "ag-mem-48.quota_query":
            self._handle_quota_query(msg)
            return

        # 清理完成回执
        if msg.topic == "ag-mem-48.cleanup_complete":
            self._handle_cleanup_complete(msg.data)
            return

        # 配额退还请求
        if msg.topic == "ag-mem-48.quota_return":
            released = msg.data.get("released_bytes", 0)
            self._total_usage_bytes = max(0, self._total_usage_bytes - released)
            return

    # ====================== 用量更新与状态检查 ======================
    def _handle_storage_report(self, data: Dict[str, Any]):
        layer = data.get("source_layer", "")
        if layer:
            self._layer_usage[layer] = {
                "entry_count": data.get("total_entries", 0),
                "usage_bytes": data.get("total_entries", 0) * 1024,
                "usage_pct": data.get("usage_pct", 0),
            }
        self._total_usage_bytes = sum(
            l.get("usage_bytes", 0) for l in self._layer_usage.values()
        )
        total_pct = self._calculate_total_usage_pct()

        if total_pct >= self.CRITICAL_THRESHOLD and self.state != QuotaState.CAPACITY_CRITICAL:
            self.state = QuotaState.CAPACITY_CRITICAL
            self._trigger_critical_response(total_pct)
        elif total_pct >= self.WARN_THRESHOLD and self.state == QuotaState.NORMAL:
            self.state = QuotaState.CAPACITY_WARNING
            self._trigger_warning_response(total_pct)

    def _trigger_warning_response(self, usage_pct: float):
        self._send_alert("警告", usage_pct)
        target_release = max(0, self._total_usage_bytes - int(self._total_capacity_bytes * self.TARGET_WARN_RELEASE_PCT))
        if target_release > 0:
            self._send_cleanup_command(["L1", "L2"], "温和", target_release)

    def _trigger_critical_response(self, usage_pct: float):
        self._send_alert("紧急", usage_pct)
        if not self._write_paused:
            self._write_paused = True
            self._send_write_gate_notice(True, ["L1", "L2"], "容量紧急，暂停非关键写入")
        target_release = max(0, self._total_usage_bytes - int(self._total_capacity_bytes * self.TARGET_CRITICAL_RELEASE_PCT))
        if target_release > 0:
            self._send_cleanup_command(["L1", "L2", "L3"], "强制", target_release)

    def _handle_cleanup_complete(self, data: Dict[str, Any]):
        released = data.get("total_released_bytes", 0)
        self._total_usage_bytes = max(0, self._total_usage_bytes - released)
        total_pct = self._calculate_total_usage_pct()

        if self.state == QuotaState.CAPACITY_CRITICAL and total_pct < self.WARN_THRESHOLD:
            self.state = QuotaState.CAPACITY_WARNING
            self._restore_writes()
        if self.state == QuotaState.CAPACITY_WARNING and total_pct < self.WARN_THRESHOLD - 0.05:
            self.state = QuotaState.NORMAL

    def _restore_writes(self):
        if self._write_paused:
            self._write_paused = False
            self._send_write_gate_notice(False, ["L1", "L2"], "容量恢复正常，恢复写入")

    # ====================== 查询处理 ======================
    def _handle_quota_query(self, msg: Message):
        target_layer = msg.data.get("query_layer", "")
        if target_layer and target_layer in self._layer_usage:
            layer = self._layer_usage[target_layer]
            result = {
                "query_layer": target_layer,
                "current_usage_pct": layer["usage_pct"],
                "available_space_bytes": self._total_capacity_bytes - self._total_usage_bytes,
                "quota_limit_bytes": self._total_capacity_bytes
            }
        else:
            result = {
                "query_layer": "全局",
                "current_usage_pct": self._calculate_total_usage_pct(),
                "available_space_bytes": self._total_capacity_bytes - self._total_usage_bytes,
                "quota_limit_bytes": self._total_capacity_bytes
            }
        if self.bus:
            self.bus.publish(
                topic=f"{msg.source_module}.quota_result",
                source_module=self.module_id,
                data=result,
                target_module=msg.source_module,
                correlation_id=msg.correlation_id
            )

    # ====================== 消息发送辅助 ======================
    def _send_alert(self, level: str, usage_pct: float):
        if self.bus:
            self.bus.publish_to_module("ag-mem-01", "capacity_alert", self.module_id, {
                "alert_level": level,
                "current_usage_pct": usage_pct,
                "suggested_action": "触发清理" if level == "警告" else "暂停写入并强制清理"
            })

    def _send_cleanup_command(self, layers: List[str], strategy: str, target_bytes: int):
        if self.bus:
            self.bus.publish_to_module("ag-mem-42", "cleanup_command", self.module_id, {
                "target_layers": layers,
                "strategy": strategy,
                "target_release_bytes": target_bytes
            })

    def _send_write_gate_notice(self, paused: bool, layers: List[str], reason: str):
        if self.bus:
            self.bus.publish_to_module("ag-mem-03", "write_gate_notice", self.module_id, {
                "paused": paused,
                "affected_layers": layers,
                "reason": reason
            })

    # ====================== 辅助计算 ======================
    def _calculate_total_usage_pct(self) -> float:
        if self._total_capacity_bytes <= 0:
            return 0.0
        return round(min(self._total_usage_bytes / self._total_capacity_bytes, 1.0), 3)

    def _report_status(self):
        if self.bus:
            layer_pcts = {layer: info["usage_pct"] for layer, info in self._layer_usage.items()}
            self.bus.publish_to_module("ag-mem-01", "internal_status", self.module_id, {
                "state": self.state.value,
                "total_usage_pct": self._calculate_total_usage_pct(),
                "layer_usage_pct": layer_pcts,
                "available_bytes": self._total_capacity_bytes - self._total_usage_bytes
            })

    # ====================== 管理接口 ======================
    def emergency_shutdown(self):
        self.state = QuotaState.SYSTEM_PAUSED
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