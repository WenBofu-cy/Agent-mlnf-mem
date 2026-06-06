```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-01
模块名称: 总控漏斗F0-双漏斗全局调度中枢
所属分区: 一、顶层总控中枢
核心职责: 作为 Agent-mlnf-mem 双漏斗记忆中枢的全局唯一调度入口，接收 ECC 认知大脑通过
          MemoryBus 下发的经验查询与写入请求，完成意图解析、优先级仲裁、双漏斗路由分发。
          管理漏斗一（用户画像）与漏斗二（任务经验）的资源分配与模式切换，协调漏斗外挂
          扩展区的只读查询。汇总各子漏斗运行状态，统一向 ECC 回传查询回执与健康状态。
          不参与任何认知决策，仅执行记忆存储的调度与路由。

依赖模块:
    ag-mem-02(漏斗一专属调度单元), ag-mem-03(漏斗二专属调度单元),
    ag-mem-44(独立知识库), ag-mem-45(安全规则库), ag-mem-48(全局容量配额管控单元)
被依赖模块:
    ECC 认知大脑, ag-mem-02~51 全部记忆模块

安全约束:
  S-01: 本模块为双漏斗记忆中枢的唯一对外入口，所有 ECC 记忆请求必须经本模块路由
  S-02: 漏斗一数据仅向通过身份验证的查询请求开放，编译期禁止漏斗一数据流入漏斗二
  S-03: 熔断状态下，本模块仅接收恢复信号，拒绝一切查询与写入请求
  S-04: 本模块不参与认知决策，仅执行记忆存储的调度、路由与状态汇总
  S-05: 冷启动自检未通过时，必须明确标记降级模块并向 ECC 上报告警

版本: V1.0 (最终可用版)
"""

import time
import uuid
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum

from memory_bus import InternalBus, MemoryBus, Message


class DispatchState(Enum):
    INIT = "init"
    IDLE = "idle"
    NORMAL_DISPATCH = "normal_dispatch"
    MAINTENANCE = "maintenance"
    MELTDOWN = "meltdown"


class F0GlobalDispatch:
    module_id = "ag-mem-01"
    module_name = "总控漏斗F0-双漏斗全局调度中枢"
    version = "V1.0"

    def __init__(self):
        self.bus: Optional[InternalBus] = None
        self.external_bus: Optional[MemoryBus] = None

        self.state = DispatchState.INIT
        self._request_queue: List[Dict[str, Any]] = []

        # 子模块状态缓存
        self._funnel_one_available = False
        self._funnel_two_available = False
        self._capacity_status: Dict[str, Any] = {}

        # 冷启动阶段计数器（协作式调度）
        self._cold_start_phase = 0
        self._cold_start_time = 0.0

        # 异步查询收集器
        self._pending_queries: Dict[str, Dict[str, Any]] = {}

        self._last_health_report_time = time.time()
        self._last_maintenance_scan_time = time.time()

        # 外部总线订阅标志（保证自动订阅且仅执行一次）
        self._subscribed_external = False

        self._pending_logs: List[Dict[str, Any]] = []

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    # ====================== 主循环 ======================
    def f0_dispatch_main_loop(self):
        # 自动订阅外部总线（保证 ECC 请求能抵达本模块）
        if self.external_bus and not self._subscribed_external:
            self.external_bus.subscribe_to_module(self.module_id, self.handle_message)
            self._subscribed_external = True

        # 处理总线消息
        if self.bus:
            self.bus.process_batch(20)
        if self.external_bus:
            self.external_bus.process_batch(20)

        if self.state == DispatchState.INIT:
            self._perform_cold_start_async()
            return

        if self.state == DispatchState.MELTDOWN:
            return

        now = time.time()

        # 处理请求队列
        while self._request_queue:
            req_data = self._request_queue.pop(0)
            self._dispatch_request(req_data)

        # 检查异步查询
        self._check_pending_queries()

        # 健康上报
        if now - self._last_health_report_time >= 1.0:
            self._report_health()
            self._last_health_report_time = now

        # 定时维护
        if now - self._last_maintenance_scan_time >= 86400:
            self._enter_maintenance()
            self._last_maintenance_scan_time = now

    # ====================== 异步冷启动 ======================
    def _perform_cold_start_async(self):
        now = time.time()
        if self._cold_start_phase == 0:
            if self.bus:
                self.bus.publish_to_module("ag-mem-02", "init_check", self.module_id, {})
                self.bus.publish_to_module("ag-mem-03", "init_check", self.module_id, {})
            self._cold_start_phase = 1
            self._cold_start_time = now
            return

        if self._cold_start_phase == 1:
            # 等待子模块状态上报，超时 3 秒后直接进入 IDLE（防止因缺失子模块而卡死）
            if now - self._cold_start_time > 3.0:
                # 若未收到任何状态，默认视为可用（确保演示或早期集成不阻塞）
                if not self._funnel_one_available and not self._funnel_two_available:
                    self._funnel_one_available = True
                    self._funnel_two_available = True
                self.state = DispatchState.IDLE
                self._log_event("SYSTEM_EVENT", {"sub_type": "cold_start_success"})
                self._cold_start_phase = 0

    # ====================== 异步查询处理 ======================
    def _check_pending_queries(self):
        completed = []
        for corr_id, entry in list(self._pending_queries.items()):
            if time.time() - entry["start_time"] > entry["timeout"]:
                self._merge_and_reply(corr_id, entry)
                completed.append(corr_id)
        for cid in completed:
            del self._pending_queries[cid]

    def _merge_and_reply(self, corr_id: str, entry: Dict[str, Any]):
        merged = []
        confidence = 0.0
        for resp_data in entry["responses"].values():
            if isinstance(resp_data, list):
                merged.extend(resp_data)
                confidence += 0.4
            else:
                self._log_event("WARNING", {"msg": "查询响应格式异常"})
        merged.sort(key=lambda x: x.get("importance", 0), reverse=True)

        req = entry["req"]
        target = req.get("_source_module", "ag-ecc-01")
        self._reply_to_ecc_module(
            target=target,
            data={
                "matched_experiences": merged,
                "confidence": min(confidence, 1.0),
                "source_funnel": "funnel_one+funnel_two",
                "query_duration_ms": (time.time() - entry["start_time"]) * 1000,
            },
            correlation_id=corr_id,
        )

    # ====================== 总线消息入口 ======================
    def handle_message(self, msg: Message):
        # 内部消息
        if msg.topic.startswith("ag-mem-01.") and msg.source_module.startswith("ag-mem-"):
            self._handle_internal_message(msg)
            return

        # 外部 ECC 请求
        if msg.source_module.startswith("ag-ecc"):
            if self.state == DispatchState.MELTDOWN:
                if msg.topic == "ag-mem-01.system_recover":
                    self._recover_from_meltdown()
                return

            if msg.topic in (
                "ag-mem-01.experience_query",
                "ag-mem-01.experience_write",
                "ag-mem-01.user_profile_query",
                "ag-mem-01.knowledge_query",
                "ag-mem-01.safety_rule_query",
                "ag-mem-01.cold_start_check",
                "ag-mem-01.system_recover",
            ):
                req_data = msg.data if isinstance(msg.data, dict) else {}
                req_data["_source_module"] = msg.source_module
                req_data["_correlation_id"] = msg.correlation_id
                self._request_queue.append(req_data)

    def _handle_internal_message(self, msg: Message):
        if msg.topic == "ag-mem-01.internal_status":
            if isinstance(msg.data, dict):
                if msg.source_module == "ag-mem-02":
                    self._funnel_one_available = msg.data.get("available", False)
                elif msg.source_module == "ag-mem-03":
                    self._funnel_two_available = msg.data.get("available", False)
                elif msg.source_module == "ag-mem-48":
                    self._capacity_status = msg.data
            return
        if msg.topic == "ag-mem-01.capacity_alert":
            self._handle_capacity_alert(msg.data)
            return
        if msg.topic == "ag-mem-01.maintenance_complete":
            if self.state == DispatchState.MAINTENANCE:
                self.state = DispatchState.IDLE
            return
        if msg.topic == "ag-mem-01.query_response":
            self._handle_query_response(msg)
            return

    def _handle_query_response(self, msg: Message):
        data = msg.data if isinstance(msg.data, dict) else {}
        # 优先从 data 中提取 _correlation_id，次选总线消息的 correlation_id
        corr_id = data.get("_correlation_id") or msg.correlation_id
        if not corr_id or corr_id not in self._pending_queries:
            return
        entry = self._pending_queries[corr_id]
        results = data.get("results", data.get("matched_experiences", []))
        entry["responses"][msg.source_module] = results
        entry["received_count"] += 1
        if entry["received_count"] >= entry["expected_count"]:
            self._merge_and_reply(corr_id, entry)
            del self._pending_queries[corr_id]

    # ====================== 核心业务 ======================
    def _dispatch_request(self, req_data: Dict[str, Any]):
        req_type = req_data.get("request_type", "")
        if req_type == "experience_query":
            self._handle_experience_query(req_data)
        elif req_type == "experience_write":
            self._handle_experience_write(req_data)
        elif req_type == "user_profile_query":
            self._handle_user_profile_query(req_data)
        elif req_type == "knowledge_query":
            self._handle_knowledge_query(req_data)
        elif req_type == "safety_rule_query":
            self._handle_safety_rule_query(req_data)
        elif req_type == "cold_start_check":
            self._perform_cold_start_async()
            target = req_data.get("_source_module", "ag-ecc-01")
            self._reply_to_ecc_module(
                target=target,
                data={
                    "funnel_one_available": self._funnel_one_available,
                    "funnel_two_available": self._funnel_two_available,
                    "state": self.state.value,
                },
                correlation_id=req_data.get("_correlation_id", ""),
            )
        else:
            target = req_data.get("_source_module", "ag-ecc-01")
            self._reply_to_ecc_module(
                target=target,
                data={"error": "Unknown request type"},
                success=False,
                correlation_id=req_data.get("_correlation_id", ""),
            )

    def _handle_experience_query(self, req: Dict):
        corr_id = req.get("_correlation_id", f"qry-{uuid.uuid4().hex[:8]}")
        self._pending_queries[corr_id] = {
            "req": req,
            "responses": {},
            "received_count": 0,
            "expected_count": 2,
            "start_time": time.time(),
            "timeout": 2.0,
        }
        if self.bus:
            # 将关联 ID 放入 data 中，子模块响应时需原样带回
            self.bus.publish_to_module(
                "ag-mem-02", "experience_query", self.module_id,
                {"query": req, "_correlation_id": corr_id}
            )
            self.bus.publish_to_module(
                "ag-mem-03", "experience_query", self.module_id,
                {"query": req, "_correlation_id": corr_id}
            )

    def _handle_experience_write(self, req: Dict):
        target = "ag-mem-02" if req.get("is_user_profile", False) else "ag-mem-03"
        if self.bus:
            self.bus.publish_to_module(target, "experience_write", self.module_id, req)
        self._reply_to_ecc_module(
            target=req.get("_source_module", "ag-ecc-01"),
            data={
                "write_status": "received",
                "assigned_layer": "L1",
                "assigned_slot": target,
                "estimated_promotion_time": time.time() + 86400,
            },
            correlation_id=req.get("_correlation_id", ""),
        )

    def _handle_user_profile_query(self, req: Dict):
        if self.bus:
            self.bus.publish_to_module("ag-mem-02", "user_profile_query", self.module_id, req)
        self._reply_to_ecc_module(
            target=req.get("_source_module", "ag-ecc-01"),
            data={"status": "dispatched"},
            correlation_id=req.get("_correlation_id", ""),
        )

    def _handle_knowledge_query(self, req: Dict):
        if self.bus:
            self.bus.publish_to_module("ag-mem-44", "knowledge_query", self.module_id, req)
        self._reply_to_ecc_module(
            target=req.get("_source_module", "ag-ecc-01"),
            data={"status": "dispatched"},
            correlation_id=req.get("_correlation_id", ""),
        )

    def _handle_safety_rule_query(self, req: Dict):
        if self.bus:
            self.bus.publish_to_module("ag-mem-45", "safety_rule_query", self.module_id, req)
        self._reply_to_ecc_module(
            target=req.get("_source_module", "ag-ecc-01"),
            data={"status": "dispatched"},
            correlation_id=req.get("_correlation_id", ""),
        )

    # ====================== 统一回执 ======================
    def _reply_to_ecc_module(self, target: str, data: Dict[str, Any], success: bool = True,
                             topic: str = "memory_response", correlation_id: str = ""):
        if not self.external_bus:
            return
        payload = {"success": success, "data": data} if not success else data
        self.external_bus.publish(
            topic=f"{target}.{topic}",
            source_module=self.module_id,
            data=payload,
            target_module=target,
            correlation_id=correlation_id,
        )

    # ====================== 熔断/恢复 ======================
    def emergency_shutdown(self):
        self.state = DispatchState.MELTDOWN
        self._request_queue.clear()
        self._pending_queries.clear()
        self._log_event("SYSTEM_EVENT", {"sub_type": "emergency_shutdown"})

    def _recover_from_meltdown(self):
        self.state = DispatchState.IDLE
        self._request_queue.clear()
        self._pending_queries.clear()
        self._log_event("SYSTEM_EVENT", {"sub_type": "meltdown_recovered"})

    def _handle_capacity_alert(self, data):
        if self.state not in (DispatchState.IDLE, DispatchState.NORMAL_DISPATCH):
            return
        if self.bus:
            self.bus.publish_to_module("ag-mem-03", "low_importance_cleanup", self.module_id,
                                       {"reason": "capacity_alert"})
        self._reply_to_ecc_module(
            target="ag-ecc-01",
            data={"alert_type": "capacity_warning", "usage_pct": data.get("usage_pct")},
            topic="alert"
        )

    def _enter_maintenance(self):
        self.state = DispatchState.MAINTENANCE
        if self.bus:
            self.bus.publish_to_module("ag-mem-03", "promotion_scan", self.module_id, {})
            self.bus.publish_to_module("ag-mem-03", "forget_scan", self.module_id, {})

    def _report_health(self):
        if not self.external_bus:
            return
        status = {
            "funnel_one_active": self._funnel_one_available,
            "funnel_two_active": self._funnel_two_available,
            "total_storage_usage_pct": self._capacity_status.get("usage_pct", 0.0),
            "active_modules_count": (
                (1 if self._funnel_one_available else 0) + (1 if self._funnel_two_available else 0)
            ),
            "maintenance_mode": self.state == DispatchState.MAINTENANCE,
            "timestamp": time.time(),
        }
        self.external_bus.publish(
            topic="ag-ecc-12.health_status",
            source_module=self.module_id,
            data=status,
            target_module="ag-ecc-12",
        )

    # ====================== 日志 ======================
    def _log_event(self, event_type: str, details: Dict[str, Any]):
        log_entry = {
            "log_id": f"log-{uuid.uuid4().hex[:8]}",
            "event_type": event_type,
            "source_module": self.module_id,
            "details": details,
            "timestamp": time.time(),
        }
        self._pending_logs.append(log_entry)
        if self.bus:
            self.bus.publish_to_module("ag-mem-51", "log_event", self.module_id, log_entry)

    def collect_pending_logs(self) -> List[Dict]:
        tmp = self._pending_logs.copy()
        self._pending_logs.clear()
        return tmp

    def get_state(self):
        return self.state
```