#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-03
模块名称: 漏斗二专属调度单元
所属分区: 一、顶层总控中枢
核心职责: 作为漏斗二（任务经验漏斗）的专属调度单元，负责任务经验分槽的全生命周期管理。
          接收 ag-mem-01 转发的任务经验查询与写入请求，依据任务场景标签判定目标分槽，
          管理对话交互槽、工具调用槽、信息检索槽、创作生成槽、通用任务槽五类分槽的激活
          与休眠。确保不同分槽之间的经验条目物理隔离，管理各分槽独立的晋升阈值与遗忘
          策略参数。不参与任何认知决策，仅执行漏斗二内部资源的调度与路由。

依赖模块:
    ag-mem-01(总控漏斗F0), ag-mem-14(任务场景判定与分槽路由单元),
    ag-mem-48(全局容量配额管控单元)
被依赖模块:
    ag-mem-01, ag-mem-15~19(五个场景分槽), ag-mem-20~43(五层存储与重要度引擎)

安全约束:
  S-01: 漏斗二数据编译期禁止包含任何用户个人身份信息，仅存储脱敏后的任务经验与策略
  S-02: 不同场景分槽之间的经验数据物理隔离，跨槽查询需通过本模块统一路由
  S-03: 漏斗二存储空间不足时，必须优先保护 L4/L5 层关键经验，仅清理 L1/L2 低重要度条目
  S-04: 本模块仅负责分槽调度与数据路由，不直接操作经验内容
  S-05: 维护扫描期间不得中断正常查询服务，写入请求排队不得超过 5 秒

版本: V1.0 (slot_result 路由修复版)
"""

import time
import uuid
from typing import Any, Dict, List, Optional
from enum import Enum

from memory_bus import InternalBus, Message


class DispatcherState(Enum):
    IDLE = "idle"
    SCENE_JUDGE = "scene_judge"
    SLOT_CREATING = "slot_creating"
    ROUTING = "routing"
    MAINT_SCAN = "maint_scan"
    SYSTEM_PAUSED = "system_paused"


class SceneCategory(Enum):
    DIALOGUE = "对话交互"
    TOOL_CALL = "工具调用"
    SEARCH = "信息检索"
    CREATION = "创作生成"
    GENERAL = "通用任务"


# 场景类别到分槽模块ID的映射
SCENE_TO_SLOT_MAP = {
    SceneCategory.DIALOGUE: "ag-mem-15",
    SceneCategory.TOOL_CALL: "ag-mem-16",
    SceneCategory.SEARCH: "ag-mem-17",
    SceneCategory.CREATION: "ag-mem-18",
    SceneCategory.GENERAL: "ag-mem-19",
}

# 场景类别默认权重配置
SCENE_WEIGHT_CONFIG = {
    SceneCategory.DIALOGUE: {"v_weight": 1.2, "s_weight": 1.0, "c_weight": 1.0},
    SceneCategory.TOOL_CALL: {"v_weight": 1.0, "s_weight": 1.2, "c_weight": 1.0},
    SceneCategory.SEARCH: {"v_weight": 1.0, "s_weight": 1.0, "c_weight": 1.1},
    SceneCategory.CREATION: {"v_weight": 1.1, "s_weight": 1.0, "c_weight": 1.0},
    SceneCategory.GENERAL: {"v_weight": 1.0, "s_weight": 1.0, "c_weight": 1.0},
}


class FunnelTwoDispatcher:
    module_id = "ag-mem-03"
    module_name = "漏斗二专属调度单元"
    version = "V1.0"

    MAINT_SCAN_INTERVAL_SEC = 24 * 3600
    MAX_PENDING_WRITES = 50
    WRITE_TIMEOUT_SEC = 5.0
    STATUS_REPORT_INTERVAL_SEC = 5.0
    SCENE_JUDGMENT_TIMEOUT_SEC = 2.0

    def __init__(self):
        self.bus: Optional[InternalBus] = None
        self.state = DispatcherState.IDLE
        self._active_slots: Dict[str, Dict[str, Any]] = {}
        self._pending_writes: List[Dict[str, Any]] = []
        self._last_maint_scan_time = time.time()
        self._last_status_report_time = time.time()
        self._pending_logs: List[Dict[str, Any]] = []

        # 异步查询聚合
        self._pending_queries: Dict[str, Dict[str, Any]] = {}

        # 异步场景判定挂起
        self._pending_scene_requests: Dict[str, Dict[str, Any]] = {}

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    # ====================== 主循环 ======================
    def funnel_two_dispatcher_main_loop(self):
        if self.state == DispatcherState.SYSTEM_PAUSED:
            return

        if self.bus:
            self.bus.process_batch(10)

        now = time.time()

        if now - self._last_maint_scan_time >= self.MAINT_SCAN_INTERVAL_SEC:
            self._perform_maintenance_scan()
            self._last_maint_scan_time = now

        # 处理写入请求队列（超时清理）
        while self._pending_writes:
            req = self._pending_writes[0]
            if now - req.get("enqueue_time", now) > self.WRITE_TIMEOUT_SEC:
                original_msg = req.get("_original_msg")
                self._pending_writes.pop(0)
                if original_msg:
                    self._reply_to_f0(original_msg, "write_receipt", {
                        "write_status": "failed",
                        "error": "写入请求排队超时"
                    })
            else:
                break

        # 检查异步查询与场景判定超时
        self._check_pending_queries()
        self._check_pending_scene_requests()

        if now - self._last_status_report_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._report_status()
            self._last_status_report_time = now

    # ====================== 总线消息入口 ======================
    def handle_message(self, msg: Message):
        if not isinstance(msg.data, dict):
            return

        if msg.topic == "ag-mem-03.init_check":
            self._handle_init_check(msg)
        elif msg.topic == "ag-mem-03.experience_query":
            self._handle_experience_query(msg)
        elif msg.topic == "ag-mem-03.experience_write":
            self._handle_experience_write(msg)
        elif msg.topic == "ag-mem-03.low_importance_cleanup":
            self._handle_capacity_cleanup(msg)
        elif msg.topic == "ag-mem-03.promotion_scan":
            self._handle_promotion_scan(msg)
        elif msg.topic == "ag-mem-03.forget_scan":
            self._handle_forget_scan(msg)
        elif msg.topic == "ag-mem-03.scene_judgment_result":
            self._handle_scene_judgment_result(msg)
        elif msg.topic == "ag-mem-03.slot_query_response":
            self._handle_slot_query_response(msg)
        elif msg.topic == "ag-mem-03.slot_result":
            self._handle_slot_result(msg)

    def _handle_init_check(self, msg: Message):
        if self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-01",
                event_type="internal_status",
                source_module=self.module_id,
                data={"available": True}
            )

    def _handle_experience_query(self, msg: Message):
        corr_id = msg.correlation_id or msg.data.get("_correlation_id", str(uuid.uuid4()))
        target_slots = list(SCENE_TO_SLOT_MAP.values())
        if not target_slots:
            self._reply_to_f0(msg, "query_response", {
                "results": [],
                "confidence": 0.0,
                "_correlation_id": corr_id
            }, corr_id)
            return

        self._pending_queries[corr_id] = {
            "req_msg": msg,
            "responses": {},
            "expected_count": len(target_slots),
            "start_time": time.time(),
            "timeout": 2.0,
            "corr_id": corr_id
        }

        for slot_id in target_slots:
            if self.bus:
                self.bus.publish_to_module(
                    target_module=slot_id,
                    event_type="experience_query",
                    source_module=self.module_id,
                    data={
                        "query": msg.data.get("query", {}),
                        "_correlation_id": corr_id
                    }
                )

    def _handle_experience_write(self, msg: Message):
        scene_label = msg.data.get("scene_label")
        if scene_label:
            slot_id = self._map_scene_label_to_slot(scene_label)
            self._route_write_to_slot(slot_id, msg)
            return

        corr_id = msg.correlation_id or str(uuid.uuid4())
        self._pending_scene_requests[corr_id] = {
            "req_msg": msg,
            "start_time": time.time(),
            "timeout": self.SCENE_JUDGMENT_TIMEOUT_SEC
        }
        if self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-14",
                event_type="judge_scene",
                source_module=self.module_id,
                data={
                    "task_description": msg.data.get("task_description", ""),
                    "context": msg.data.get("context", {}),
                    "_correlation_id": corr_id
                }
            )

    def _handle_scene_judgment_result(self, msg: Message):
        corr_id = msg.correlation_id
        if corr_id not in self._pending_scene_requests:
            return
        entry = self._pending_scene_requests.pop(corr_id)
        req_msg = entry["req_msg"]
        scene_category = msg.data.get("scene_category", SceneCategory.GENERAL.value)
        slot_id = self._map_scene_label_to_slot(scene_category)
        self._route_write_to_slot(slot_id, req_msg)

    def _handle_slot_query_response(self, msg: Message):
        corr_id = msg.data.get("_correlation_id")
        if not corr_id or corr_id not in self._pending_queries:
            return
        entry = self._pending_queries[corr_id]
        entry["responses"][msg.source_module] = msg.data.get("results", [])
        if len(entry["responses"]) >= entry["expected_count"]:
            self._merge_and_reply_query(corr_id)

    def _handle_slot_result(self, msg: Message):
        """
        接收来自各场景分槽（ag-mem-15~19）的回执，转发给 ag-mem-01。
        回执包括：写入确认、查询结果、维护结果等。
        """
        if self.bus:
            self.bus.publish(
                topic="ag-mem-01.slot_result",
                source_module=self.module_id,
                data=msg.data,
                target_module="ag-mem-01",
                correlation_id=msg.correlation_id
            )

    def _check_pending_queries(self):
        now = time.time()
        for corr_id in list(self._pending_queries.keys()):
            entry = self._pending_queries.get(corr_id)
            if entry and (now - entry["start_time"] > entry["timeout"]):
                self._merge_and_reply_query(corr_id)

    def _check_pending_scene_requests(self):
        now = time.time()
        expired = []
        for corr_id, entry in self._pending_scene_requests.items():
            if now - entry["start_time"] > entry["timeout"]:
                expired.append(corr_id)
        for corr_id in expired:
            entry = self._pending_scene_requests.pop(corr_id, None)
            if entry:
                req_msg = entry["req_msg"]
                slot_id = SCENE_TO_SLOT_MAP[SceneCategory.GENERAL]
                self._route_write_to_slot(slot_id, req_msg)
                self._log_event("SCENE_JUDGMENT_TIMEOUT", {
                    "correlation_id": corr_id,
                    "defaulted_to": SceneCategory.GENERAL.value
                })

    def _merge_and_reply_query(self, corr_id: str):
        entry = self._pending_queries.pop(corr_id, None)
        if not entry:
            return
        merged = []
        for resp in entry["responses"].values():
            if isinstance(resp, list):
                merged.extend(resp)
        merged.sort(key=lambda x: x.get("importance", 0), reverse=True)

        req_msg = entry["req_msg"]
        duration_ms = (time.time() - entry["start_time"]) * 1000
        self._reply_to_f0(req_msg, "query_response", {
            "matched_experiences": merged,
            "source_slots": list(entry["responses"].keys()),
            "confidence": 0.8 if merged else 0.0,
            "query_duration_ms": duration_ms,
            "_correlation_id": corr_id
        }, corr_id)

    def _route_write_to_slot(self, slot_id: str, msg: Message):
        if slot_id not in self._active_slots:
            self._active_slots[slot_id] = {
                "slot_id": slot_id,
                "entry_count": 0,
                "last_active": time.time()
            }
        else:
            self._active_slots[slot_id]["last_active"] = time.time()

        if self.bus:
            self.bus.publish_to_module(
                target_module=slot_id,
                event_type="experience_write",
                source_module=self.module_id,
                data=msg.data
            )

        self._reply_to_f0(msg, "write_receipt", {
            "write_status": "received",
            "assigned_slot": slot_id
        })

    def _map_scene_label_to_slot(self, scene_label: str) -> str:
        for scene, slot_id in SCENE_TO_SLOT_MAP.items():
            if scene.value == scene_label:
                return slot_id
        return SCENE_TO_SLOT_MAP[SceneCategory.GENERAL]

    # ====================== 容量清理、晋升、遗忘扫描 ======================
    def _handle_capacity_cleanup(self, msg: Message):
        if self.bus:
            self.bus.publish_to_module("ag-mem-20", "low_importance_cleanup", self.module_id, {})
            self.bus.publish_to_module("ag-mem-22", "low_importance_cleanup", self.module_id, {})

    def _handle_promotion_scan(self, msg: Message):
        if self.bus:
            self.bus.publish_to_module("ag-mem-38", "promotion_scan", self.module_id, {
                "scope": "all",
                "trigger": "scheduled"
            })

    def _handle_forget_scan(self, msg: Message):
        if self.bus:
            self.bus.publish_to_module("ag-mem-40", "forget_scan", self.module_id, {
                "scope": "all",
                "trigger": "scheduled"
            })

    def _perform_maintenance_scan(self):
        self.state = DispatcherState.MAINT_SCAN
        if self.bus:
            self.bus.publish_to_module("ag-mem-25", "merge_scan", self.module_id, {"type": "merge"})
            self.bus.publish_to_module("ag-mem-40", "forget_scan", self.module_id, {"type": "forget"})
        now = time.time()
        for info in self._active_slots.values():
            if now - info.get("last_active", 0) > 30 * 24 * 3600:
                info["is_active"] = False
        self.state = DispatcherState.IDLE

    # ====================== 状态上报 ======================
    def _report_status(self):
        if self.bus:
            self.bus.publish_to_module("ag-mem-01", "internal_status", self.module_id, {
                "available": True,
                "active_slots": len(self._active_slots),
                "slot_details": {sid: info.get("entry_count", 0) for sid, info in self._active_slots.items()}
            })

    # ====================== 回复工具 ======================
    def _reply_to_f0(self, original_msg: Message, event: str, data: dict, corr_id: str = ""):
        if not self.bus:
            return
        self.bus.publish(
            topic=f"ag-mem-01.{event}",
            source_module=self.module_id,
            data=data,
            target_module="ag-mem-01",
            correlation_id=corr_id or original_msg.correlation_id
        )

    # ====================== 熔断与日志 ======================
    def emergency_shutdown(self):
        self.state = DispatcherState.SYSTEM_PAUSED
        self._pending_writes.clear()
        self._pending_queries.clear()
        self._pending_scene_requests.clear()
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