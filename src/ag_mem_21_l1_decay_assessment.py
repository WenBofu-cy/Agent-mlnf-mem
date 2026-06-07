#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-21
模块名称: L1临时层时序衰减单元
所属分区: 三、漏斗二：任务经验漏斗 / 五层存储
核心职责: 接收 ag-mem-20（L1临时层存储单元）发起的衰减评估请求，对L1中留存超过24小时
          的条目进行时序衰减评估。基于条目的留存时长、重要度（I值）及来源场景分槽的专属
          晋升策略，判定每个条目的处理去向：晋升至L2近期层（满足晋升条件）、继续保留在L1
          （未达晋升条件但仍有价值）、或直接清除（重要度过低）。不参与条目内容修改或认知
          决策，仅执行基于时间与重要度的确定性筛选。

依赖模块:
    ag-mem-20(L1临时层存储单元), ag-mem-22(L2近期层存储单元),
    ag-mem-42(冗余记忆删除与归档单元), ag-mem-35(三维权重系数配置单元)
被依赖模块:
    ag-mem-20, ag-mem-22, ag-mem-42

安全约束:
  S-01: 衰减评估仅读取条目元数据（时间戳、I值、分槽编号），不得访问或修改条目的经验内容
  S-02: 晋升至L2的条目必须完整保留其原始来源分槽编号，用于L2层的后续管理
  S-03: 容量紧急时的阈值调整仅在本次评估中生效，不得持久化修改各分槽的默认阈值配置
  S-04: 清除条目必须通过 ag-mem-42 执行安全删除，本模块不得直接操作存储删除

版本: V1.0
"""

import time
import uuid
from typing import Any, Dict, List, Optional
from enum import Enum

from memory_bus import InternalBus, Message


class AssessmentState(Enum):
    IDLE = "idle"
    EVALUATING = "evaluating"
    OUTPUTTING = "outputting"
    SYSTEM_PAUSED = "system_paused"


class L1DecayAssessment:
    module_id = "ag-mem-21"
    module_name = "L1临时层时序衰减单元"
    version = "V1.0"

    # 时间阈值
    MIN_RETENTION_HOURS_NORMAL = 24
    MIN_RETENTION_HOURS_EMERGENCY = 6
    MAX_L1_RETENTION_HOURS = 72

    # 各分槽专属晋升/遗忘阈值
    SLOT_CONFIGS = {
        "ag-mem-15": {"promotion": 0.35, "forget": 0.08},
        "ag-mem-16": {"promotion": 0.40, "forget": 0.10},
        "ag-mem-17": {"promotion": 0.38, "forget": 0.08},
        "ag-mem-18": {"promotion": 0.38, "forget": 0.10},
        "ag-mem-19": {"promotion": 0.42, "forget": 0.06},
    }

    def __init__(self):
        self.bus: Optional[InternalBus] = None
        self.state = AssessmentState.IDLE
        self._pending_logs: List[Dict[str, Any]] = []

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    # ====================== 统一主循环入口 ======================
    def run_cycle(self):
        self.l1_decay_assessment_main_loop()

    def l1_decay_assessment_main_loop(self):
        if self.state == AssessmentState.SYSTEM_PAUSED:
            return

        if self.bus:
            self.bus.process_batch(10)

    # ====================== 总线消息入口 ======================
    def handle_message(self, msg: Message):
        if not isinstance(msg.data, dict):
            return

        if msg.topic == "ag-mem-21.decay_assessment":
            self._handle_decay_request(msg)
            return

    def _handle_decay_request(self, msg: Message):
        """处理衰减评估请求"""
        entries = msg.data.get("entries", [])
        trigger_reason = msg.data.get("trigger_reason", "定时衰减")
        l1_usage_pct = msg.data.get("l1_usage_pct", 0.0)

        if not entries:
            return

        self.state = AssessmentState.EVALUATING
        start_time = time.time()
        is_emergency = trigger_reason == "容量紧急"
        now = time.time()

        promoted = []
        cleared = []
        retained = []

        for entry in entries:
            if not isinstance(entry, dict):
                continue

            slot_id = entry.get("source_slot_id", "ag-mem-19")
            i_value = float(entry.get("i_value", 0))
            write_timestamp = float(entry.get("timestamp", now))

            retention_hours = (now - write_timestamp) / 3600.0
            config = self.SLOT_CONFIGS.get(slot_id, self.SLOT_CONFIGS["ag-mem-19"])

            min_retention = self.MIN_RETENTION_HOURS_EMERGENCY if is_emergency else self.MIN_RETENTION_HOURS_NORMAL
            forget_threshold = config["forget"] * 1.2 if is_emergency else config["forget"]

            if retention_hours >= self.MAX_L1_RETENTION_HOURS and i_value < config["promotion"]:
                cleared.append(entry)
            elif retention_hours >= min_retention:
                if i_value >= config["promotion"]:
                    promoted.append(entry)
                elif i_value < forget_threshold:
                    cleared.append(entry)
                else:
                    retained.append(entry)
            else:
                retained.append(entry)

        self.state = AssessmentState.OUTPUTTING

        if promoted and self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-22",
                event_type="promoted_entries",
                source_module=self.module_id,
                data={"entries": promoted}
            )

        if cleared and self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-42",
                event_type="clear_entries",
                source_module=self.module_id,
                data={"entries": cleared, "reason": "衰减评估清除"}
            )

        if self.bus:
            self.bus.publish(
                topic="ag-mem-20.decay_complete",
                source_module=self.module_id,
                data={
                    "removed_entry_ids": [e.get("entry_id") for e in cleared],
                    "total_evaluated": len(entries),
                    "promoted_count": len(promoted),
                    "cleared_count": len(cleared),
                    "retained_count": len(retained),
                    "evaluation_duration_ms": (time.time() - start_time) * 1000
                },
                target_module="ag-mem-20",
                correlation_id=msg.correlation_id
            )

        self._log_event("DECAY_COMPLETED", {
            "total": len(entries),
            "promoted": len(promoted),
            "cleared": len(cleared),
            "retained": len(retained),
            "emergency": is_emergency
        })

        self.state = AssessmentState.IDLE

    # ====================== 管理接口 ======================
    def emergency_shutdown(self):
        self.state = AssessmentState.SYSTEM_PAUSED
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