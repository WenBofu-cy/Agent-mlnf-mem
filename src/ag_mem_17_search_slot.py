#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-17
模块名称: 信息检索槽
所属分区: 三、漏斗二：任务经验漏斗 / 场景分槽管理
核心职责: 作为漏斗二中专门承载“信息检索”类任务经验的场景分槽。接收 ag-mem-03 路由的
          信息检索场景经验条目，负责本槽专属的权重调整（C值+10%）、轻度遗忘保护等，
          然后将经验委托给五层存储单元进行物理存储。不自行维护存储，不参与认知决策。

依赖模块:
    ag-mem-03(漏斗二专属调度单元), ag-mem-20~30(五层存储单元),
    ag-mem-35(三维权重系数配置单元)
被依赖模块:
    ag-mem-03, ag-mem-25(相似经验归并单元)

安全约束:
  S-01: 本槽位仅接受场景标签确认为“信息检索”的经验条目，其他场景条目将被拒绝
  S-02: C值权重上调仅在本槽位内生效，不得影响其他分槽的重要度计算
  S-03: 槽内经验的跨槽查询必须通过 ag-mem-03 统一路由，不得直接响应外部模块的查询
  S-04: 维护扫描期间的遗忘操作必须遵循本槽专属的遗忘保护参数（轻度保护），不得使用全局默认值

版本: V2.0 (最终修复版 · 全日志 · 熔断安全 · 规范对齐)
"""

import time
import uuid
from typing import Any, Dict, List, Optional
from enum import Enum

from memory_bus import InternalBus, Message


class SlotState(Enum):
    IDLE = "idle"
    WRITING = "writing"
    QUERYING = "querying"
    SYSTEM_PAUSED = "system_paused"


class SearchSlot:
    module_id = "ag-mem-17"
    module_name = "信息检索槽"
    version = "V2.0"

    # 专属权重：C 值上调 10%
    C_WEIGHT_BOOST = 1.1

    # 专属晋升阈值（略低于标准，促进检索经验晋升）
    PROMOTION_THRESHOLDS = {
        "L1_to_L2": 0.38,
        "L2_to_L3": 0.58,
        "L3_to_L4": 0.78,
        "L4_to_L5": 0.88,
    }

    # 专属遗忘阈值（轻度保护：各层阈值下调，更不易遗忘）
    FORGET_THRESHOLDS = {
        "L1": 0.08,
        "L2": 0.18,
        "L3": 0.28,
        "L4": 0.20,
    }

    def __init__(self):
        self.bus: Optional[InternalBus] = None
        self.state = SlotState.IDLE

        # 仅保留元数据计数，不存储经验实体
        self._total_entries: int = 0
        self._layer_counts = {"L1": 0, "L2": 0, "L3": 0, "L4": 0, "L5": 0}

        self._last_status_time = time.time()
        self._pending_logs: List[Dict[str, Any]] = []

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成, "
              f"C值权重上调10%, 遗忘保护=轻度, 存储委托至五层单元")

    # ====================== 全系统统一主循环入口 ======================
    def run_cycle(self):
        self.search_slot_main_loop()

    def search_slot_main_loop(self):
        if self.state == SlotState.SYSTEM_PAUSED:
            return

        if self.bus:
            self.bus.process_batch(10)

        now = time.time()
        if now - self._last_status_time >= 60:
            self._report_status()
            self._last_status_time = now

    # ====================== 总线消息入口 ======================
    def handle_message(self, msg: Message):
        if not isinstance(msg.data, dict):
            return

        if msg.topic == "ag-mem-17.experience_write":
            self._handle_write(msg)
            return

        if msg.topic == "ag-mem-17.experience_query":
            self._handle_query(msg)
            return

        if msg.topic == "ag-mem-17.maintenance":
            self._handle_maintenance(msg)
            return

    def _handle_write(self, msg: Message):
        """处理经验写入：调整 C 值后委托给 L1 存储单元"""
        data = msg.data
        scene_label = data.get("scene_label", "")

        if scene_label != "信息检索":
            self._reply_write_confirm(msg, "", "L1", 0, False, "场景标签不匹配")
            self._log_event("WRITE_REJECTED", {"reason": "场景不匹配", "received": scene_label})
            return

        self.state = SlotState.WRITING
        start_time = time.time()

        # 提取分量
        experience_data = data.get("experience_data", {})
        i0_value = float(data.get("i0_value", 0.0))
        s_value = float(data.get("s_value", 0.0))
        v_value = float(data.get("v_value", 0.0))
        c_value = float(data.get("c_value", 0.0))
        result_label = data.get("result_label", "成功")

        # C 值上调 (S-02)
        adjusted_c = min(c_value * self.C_WEIGHT_BOOST, 1.0)

        # 构建存储载荷
        storage_payload = {
            "experience_data": experience_data,
            "i0_value": i0_value,
            "s_value": s_value,
            "v_value": v_value,
            "c_value": adjusted_c,
            "result_label": result_label,
            "source_slot": self.module_id,
            "correlation_id": msg.correlation_id,
        }

        # 委托 L1 存储
        if self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-20",
                event_type="experience_write",
                source_module=self.module_id,
                data=storage_payload
            )

        # 更新本地元数据计数
        self._layer_counts["L1"] += 1
        self._total_entries += 1

        elapsed_ms = (time.time() - start_time) * 1000
        self._reply_write_confirm(msg, "", "L1", elapsed_ms, True)
        self._log_event("EXPERIENCE_WRITTEN", {
            "layer": "L1",
            "c_original": round(c_value, 2),
            "c_adjusted": round(adjusted_c, 2)
        })
        self.state = SlotState.IDLE

    def _handle_query(self, msg: Message):
        """查询委托：转发给 ag-mem-03 统一路由"""
        self.state = SlotState.QUERYING
        if self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-03",
                event_type="experience_query",
                source_module=self.module_id,
                data={**msg.data, "source_slot": self.module_id},
            )
        self._log_event("QUERY_FORWARDED", {"to": "ag-mem-03"})
        self.state = SlotState.IDLE

    def _handle_maintenance(self, msg: Message):
        """将本槽专属阈值上报给 ag-mem-03，由调度单元协调存储层执行"""
        if self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-03",
                event_type="slot_maintenance",
                source_module=self.module_id,
                data={
                    "promotion_thresholds": self.PROMOTION_THRESHOLDS,
                    "forget_thresholds": self.FORGET_THRESHOLDS,
                }
            )
        self._log_event("MAINTENANCE_FORWARDED", {"thresholds": self.FORGET_THRESHOLDS})

    # ====================== 回复工具 ======================
    def _reply_write_confirm(self, msg: Message, entry_id: str, layer: str,
                             duration_ms: float, success: bool, error: str = ""):
        if not self.bus:
            return
        self.bus.publish(
            topic="ag-mem-03.slot_result",
            source_module=self.module_id,
            data={
                "success": success,
                "entry_id": entry_id,
                "assigned_layer": layer,
                "write_duration_ms": duration_ms,
                "error": error
            },
            target_module="ag-mem-03",
            correlation_id=msg.correlation_id
        )

    def _report_status(self):
        if self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-03",
                event_type="internal_status",
                source_module=self.module_id,
                data={
                    "total_entries": self._total_entries,
                    "layer_distribution": self._layer_counts.copy()
                }
            )

    # ====================== 管理接口 ======================
    def emergency_shutdown(self):
        self.state = SlotState.SYSTEM_PAUSED
        self._total_entries = 0
        self._layer_counts = {k: 0 for k in self._layer_counts}
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