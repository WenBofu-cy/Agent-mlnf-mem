#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-20
模块名称: L1临时层存储单元
所属分区: 三、漏斗二：任务经验漏斗 / 五层存储
核心职责: 作为漏斗二五层记忆存储架构的第一层，负责接收并存储本次会话或近期产生的瞬时
          任务经验片段。L1层是经验进入漏斗二存储系统的唯一入口，所有新经验均首先写入L1。
          管理L1层的容量上限，当逼近容量上限时触发低重要度条目清理或向ag-mem-21请求
          衰减评估。不参与晋升判定或遗忘决策，仅执行经验的接收、暂存与基础管理。

依赖模块:
    ag-mem-15~19(各场景分槽), ag-mem-21(L1衰减评估单元),
    ag-mem-48(全局容量配额管控单元)
被依赖模块:
    ag-mem-15~19, ag-mem-21, ag-mem-22(L2近期层存储单元)

安全约束:
  S-01: L1层仅为经验临时暂存区，不得在L1层对经验内容进行任何修改或加工
  S-02: 写入经验时必须保留原始来源分槽编号，用于后续晋升时溯源
  S-03: L1容量紧急时强制清理不得删除最近500条经验，确保当前会话的经验不丢失
  S-04: L1存储的持久化写入必须保证原子性，写入中断时不产生损坏的半条记录

版本: V1.0
"""

import time
import uuid
from typing import Any, Dict, List, Optional
from enum import Enum

from memory_bus import InternalBus, Message


class StorageState(Enum):
    NORMAL = "normal"
    CAPACITY_WARNING = "capacity_warning"
    CAPACITY_CRITICAL = "capacity_critical"
    SYSTEM_PAUSED = "system_paused"


class L1TemporaryStorage:
    module_id = "ag-mem-20"
    module_name = "L1临时层存储单元"
    version = "V1.0"

    # 容量配置
    L1_CAPACITY_RATIO = 0.60
    MAX_ENTRIES = 10000
    MAX_ENTRY_SIZE_BYTES = 10 * 1024
    CAPACITY_WARN_THRESHOLD = 0.80
    CAPACITY_CRITICAL_THRESHOLD = 0.95
    MIN_RETAIN_ENTRIES = 500
    DECAY_CHECK_INTERVAL_SEC = 6 * 3600
    STATUS_REPORT_INTERVAL_SEC = 30
    DECAY_TRIGGER_RATIO = 0.20

    def __init__(self):
        self.bus: Optional[InternalBus] = None
        self.state = StorageState.NORMAL

        # 按写入时间排序的条目列表（用于保留最近500条）
        self._entries: List[Dict[str, Any]] = []
        self._entry_count: int = 0
        self._last_decay_time = time.time()
        self._last_status_time = time.time()
        self._slot_write_stats: Dict[str, int] = {}
        self._pending_logs: List[Dict[str, Any]] = []

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成, "
              f"最大条目={self.MAX_ENTRIES}")

    # ====================== 统一主循环入口 ======================
    def run_cycle(self):
        self.l1_storage_main_loop()

    def l1_storage_main_loop(self):
        if self.state == StorageState.SYSTEM_PAUSED:
            return

        if self.bus:
            self.bus.process_batch(10)

        now = time.time()

        # 定时衰减评估
        if now - self._last_decay_time >= self.DECAY_CHECK_INTERVAL_SEC:
            self._trigger_decay("定时衰减")
            self._last_decay_time = now

        # 定期状态上报
        if now - self._last_status_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._report_status()
            self._last_status_time = now

    # ====================== 总线消息入口 ======================
    def handle_message(self, msg: Message):
        if not isinstance(msg.data, dict):
            return

        # 经验写入请求（来自各场景分槽）
        if msg.topic == "ag-mem-20.experience_write":
            self._handle_write(msg)
            return

        # 衰减清理完成确认（来自 ag-mem-21）
        if msg.topic == "ag-mem-20.cleanup_confirm":
            cleaned = msg.data.get("cleaned_count", 0)
            self._entry_count -= cleaned
            # 更新状态
            usage_pct = self._calculate_usage_pct()
            if usage_pct < self.CAPACITY_WARN_THRESHOLD:
                self.state = StorageState.NORMAL
            elif usage_pct < self.CAPACITY_CRITICAL_THRESHOLD:
                self.state = StorageState.CAPACITY_WARNING
            return

    def _handle_write(self, msg: Message):
        """处理经验写入请求"""
        data = msg.data
        source_slot = data.get("source_slot", msg.source_module)

        # 提取经验数据
        entry = {
            "entry_id": f"L1-{int(time.time()*1000)}-{uuid.uuid4().hex[:8]}",
            "source_slot_id": source_slot,
            "experience_data": data.get("experience_data", {}),
            "i0_value": float(data.get("i0_value", 0.0)),
            "s_value": float(data.get("s_value", 0.0)),
            "v_value": float(data.get("v_value", 0.0)),
            "c_value": float(data.get("c_value", 0.0)),
            "i_value": float(data.get("i_value", data.get("i0_value", 0.0))),
            "result_label": data.get("result_label", "成功"),
            "timestamp": time.time()
        }

        # 容量状态检查
        usage_pct = self._calculate_usage_pct()
        if usage_pct >= self.CAPACITY_CRITICAL_THRESHOLD:
            self.state = StorageState.CAPACITY_CRITICAL
        elif usage_pct >= self.CAPACITY_WARN_THRESHOLD:
            self.state = StorageState.CAPACITY_WARNING

        # 容量紧急处理
        if self.state == StorageState.CAPACITY_CRITICAL and self._entry_count >= self.MAX_ENTRIES:
            self._trigger_decay("容量紧急")
            if self._entry_count >= self.MAX_ENTRIES:
                self._reply_write_confirm(msg, "", 0, False, "L1容量已满且无法清理")
                return

        # 容量预警处理（温和清理）
        if self.state == StorageState.CAPACITY_WARNING:
            self._trigger_decay("容量预警")

        # 校验条目大小
        entry_size = len(str(entry["experience_data"]))
        if entry_size > self.MAX_ENTRY_SIZE_BYTES:
            self._reply_write_confirm(msg, "", 0, False,
                                      f"条目大小超过{self.MAX_ENTRY_SIZE_BYTES}字节上限")
            return

        # 写入
        self._entries.append(entry)
        self._entry_count += 1

        # 更新分槽写入统计
        if source_slot not in self._slot_write_stats:
            self._slot_write_stats[source_slot] = 0
        self._slot_write_stats[source_slot] += 1

        usage_pct = self._calculate_usage_pct()
        self._reply_write_confirm(msg, entry["entry_id"], usage_pct, True)
        self._log_event("EXPERIENCE_WRITTEN", {"entry_id": entry["entry_id"]})

    def _trigger_decay(self, reason: str):
        """触发衰减评估"""
        if self._entry_count == 0 or not self.bus:
            return

        # 按写入时间排序，取最旧的条目（保留最近500条）
        sorted_entries = sorted(self._entries, key=lambda e: e.get("timestamp", 0))
        # 不能超过 MIN_RETAIN_ENTRIES
        max_clean = max(0, self._entry_count - self.MIN_RETAIN_ENTRIES)
        decay_count = min(max_clean, max(1, int(self._entry_count * self.DECAY_TRIGGER_RATIO)))
        decay_entries = sorted_entries[:decay_count]

        if decay_entries:
            self.bus.publish_to_module(
                target_module="ag-mem-21",
                event_type="decay_assessment",
                source_module=self.module_id,
                data={
                    "entries": decay_entries,
                    "trigger_reason": reason,
                    "l1_usage_pct": self._calculate_usage_pct()
                }
            )

    def _calculate_usage_pct(self) -> float:
        if self.MAX_ENTRIES == 0:
            return 0.0
        return min(self._entry_count / self.MAX_ENTRIES, 1.0)

    def _reply_write_confirm(self, msg: Message, entry_id: str,
                             usage_pct: float, success: bool, error: str = ""):
        if not self.bus:
            return
        self.bus.publish(
            topic=f"{msg.source_module}.write_receipt",
            source_module=self.module_id,
            data={
                "success": success,
                "entry_id": entry_id,
                "l1_usage_pct": round(usage_pct, 3),
                "error_reason": error
            },
            target_module=msg.source_module,
            correlation_id=msg.correlation_id
        )

    def _report_status(self):
        if self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-48",
                event_type="storage_status",
                source_module=self.module_id,
                data={
                    "state": self.state.value,
                    "total_entries": self._entry_count,
                    "usage_pct": self._calculate_usage_pct(),
                    "write_distribution": self._slot_write_stats.copy()
                }
            )

    def get_entry_count(self) -> int:
        return self._entry_count

    # ====================== 管理接口 ======================
    def emergency_shutdown(self):
        self.state = StorageState.SYSTEM_PAUSED
        self._entries.clear()
        self._entry_count = 0
        self._slot_write_stats.clear()
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