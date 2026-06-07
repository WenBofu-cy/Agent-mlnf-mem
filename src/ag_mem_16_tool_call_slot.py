#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-16
模块名称: 工具调用槽
所属分区: 三、漏斗二：任务经验漏斗 / 场景分槽管理
核心职责: 作为漏斗二中专门承载“工具调用”类任务经验的场景分槽。接收 ag-mem-03 路由的
          工具调用场景经验条目，管理该场景下的五层记忆存储（L1-L5）。在本槽内，安全显著性
          （S值）权重自动上调20%，以强化安全相关操作经验的优先留存。同时管理该槽专属的晋升
          阈值与遗忘策略参数。不参与认知决策，仅执行工具调用类经验的存储、检索与生命周期管理。

依赖模块:
    ag-mem-03(漏斗二专属调度单元), ag-mem-20~30(五层存储单元),
    ag-mem-35(三维权重系数配置单元), ag-mem-43(失败经验安全仲裁三道校验单元)
被依赖模块:
    ag-mem-03, ag-mem-25(相似经验归并单元)

安全约束:
  S-01: 本槽位仅接受场景标签确认为“工具调用”的经验条目
  S-02: S值权重上调仅在本槽位内生效，不得影响其他分槽的重要度计算
  S-03: 失败经验必须标记待安全校验，在未通过ag-mem-43校验前不得晋升至L2及以上
  S-04: 检测到敏感操作时必须自动提升S值确保高留存优先级
  S-05: 槽内经验的跨槽查询必须通过 ag-mem-03 统一路由

版本: V1.0 (最终修复版 · 全日志 · 熔断安全 · 规范对齐)
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
    MAINTENANCE = "maintenance"
    SYSTEM_PAUSED = "system_paused"


class ToolCallSlot:
    module_id = "ag-mem-16"
    module_name = "工具调用槽"
    version = "V1.0"

    # 专属权重：S 值上调 20%
    S_WEIGHT_BOOST = 1.2
    # S 值直达 L5 阈值
    L5_DIRECT_S_THRESHOLD = 0.9

    # 专属晋升阈值
    PROMOTION_THRESHOLDS = {
        "L1_to_L2": 0.40,
        "L2_to_L3": 0.60,
        "L3_to_L4": 0.80,
        "L4_to_L5": 0.90,
    }

    # 专属遗忘阈值
    FORGET_THRESHOLDS = {
        "L1": 0.10,
        "L2": 0.25,
        "L3": 0.35,
        "L4": 0.25,
    }

    # 敏感操作列表
    SENSITIVE_OPS = [
        "delete", "write_system", "modify_permission", "db_write", "shell_exec"
    ]

    def __init__(self):
        self.bus: Optional[InternalBus] = None
        self.state = SlotState.IDLE

        # 五层存储（字典结构）
        self._entries: Dict[str, Dict[str, Dict[str, Any]]] = {
            "L1": {}, "L2": {}, "L3": {}, "L4": {}, "L5": {}
        }
        self._layer_counts = {"L1": 0, "L2": 0, "L3": 0, "L4": 0, "L5": 0}
        self._total_entries: int = 0

        self._last_status_time = time.time()
        self._pending_logs: List[Dict[str, Any]] = []

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成, "
              f"S值权重上调20%, 专属晋升/遗忘阈值已加载, L5直达已启用")

    # ====================== 全系统统一主循环入口 ======================
    def run_cycle(self):
        self.tool_call_slot_main_loop()

    def tool_call_slot_main_loop(self):
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

        if msg.topic == "ag-mem-16.experience_write":
            self._handle_write(msg)
            return

        if msg.topic == "ag-mem-16.experience_query":
            self._handle_query(msg)
            return

        if msg.topic == "ag-mem-16.maintenance":
            self._handle_maintenance(msg)
            return

    def _handle_write(self, msg: Message):
        """处理经验写入请求"""
        data = msg.data
        scene_label = data.get("scene_label", "")

        # 校验场景标签 (S-01)
        if scene_label != "工具调用":
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
        input_i_value = float(data.get("i_value", i0_value))

        # 应用 S 值上调 (S-02)
        adjusted_s = min(s_value * self.S_WEIGHT_BOOST, 1.0)

        # 敏感操作自动提升 S 值 (S-04)
        if self._is_sensitive_operation(experience_data):
            adjusted_s = max(adjusted_s, 0.8)
            self._log_event("SENSITIVE_OP_DETECTED", {"s_adjusted": round(adjusted_s, 2)})

        # 失败经验标记 (S-03)
        needs_safety_check = result_label in ("失败", "策略失误")

        # L5 直达判定
        l5_direct = False
        if adjusted_s >= self.L5_DIRECT_S_THRESHOLD and result_label == "成功":
            assigned_layer = "L5"
            l5_direct = True
        elif needs_safety_check:
            assigned_layer = "L1"  # 失败经验锁定在 L1
        else:
            assigned_layer = "L1"

        entry_id = f"L{assigned_layer[-1]}-TOOL-{uuid.uuid4().hex[:8]}"
        entry = {
            "entry_id": entry_id,
            "experience_data": experience_data,
            "i0_value": i0_value,
            "i_value": input_i_value,        # 补充完整字段
            "s_value": adjusted_s,
            "v_value": v_value,
            "c_value": c_value,
            "result_label": result_label,
            "needs_safety_check": needs_safety_check,
            "source_slot": self.module_id,
            "timestamp": time.time()
        }

        # 写入对应的层级存储
        self._entries[assigned_layer][entry_id] = entry
        self._layer_counts[assigned_layer] += 1
        self._total_entries += 1

        elapsed_ms = (time.time() - start_time) * 1000
        self._reply_write_confirm(msg, entry_id, assigned_layer, elapsed_ms, True,
                                  safety_check=needs_safety_check,
                                  l5_direct=l5_direct)
        self._log_event("EXPERIENCE_WRITTEN", {
            "entry_id": entry_id,
            "layer": assigned_layer,
            "l5_direct": l5_direct,
            "safety_check": needs_safety_check,
            "s_final": round(adjusted_s, 2)
        })
        self.state = SlotState.IDLE

    def _handle_query(self, msg: Message):
        """处理经验查询请求"""
        self.state = SlotState.QUERYING
        start_time = time.time()

        conditions = msg.data.get("query", {})
        keywords = conditions.get("keywords", [])
        max_results = msg.data.get("max_results", 20)

        matched = []
        # 从 L5 到 L1 依次检索（核心层优先）
        for layer in ["L5", "L4", "L3", "L2", "L1"]:
            for entry in self._entries[layer].values():
                if keywords:
                    text = str(entry.get("experience_data", ""))
                    if not any(kw in text for kw in keywords):
                        continue
                matched.append({**entry, "layer": layer})
                if len(matched) >= max_results:
                    break
            if len(matched) >= max_results:
                break

        # 按 S 值降序，其次 I 值
        matched.sort(key=lambda x: (x.get("s_value", 0), x.get("i_value", 0)), reverse=True)
        matched = matched[:max_results]

        elapsed_ms = (time.time() - start_time) * 1000
        self._reply_query_result(msg, matched, elapsed_ms)
        self._log_event("QUERY_DONE", {"hit_count": len(matched), "keywords": keywords})
        self.state = SlotState.IDLE

    def _handle_maintenance(self, msg: Message):
        """
        执行维护扫描（使用专属遗忘/晋升阈值）
        生成遗忘和晋升候选清单，上报给 ag-mem-03 统一决策
        """
        self.state = SlotState.MAINTENANCE
        self._log_event("MAINTENANCE_START", {"slot": self.module_id})

        forget_candidates = []
        promote_candidates = []

        for layer in ["L1", "L2"]:
            forget_threshold = self.FORGET_THRESHOLDS.get(layer, 0.10)
            if layer == "L1":
                promote_threshold = self.PROMOTION_THRESHOLDS["L1_to_L2"]
                target_layer = "L2"
            else:
                promote_threshold = self.PROMOTION_THRESHOLDS["L2_to_L3"]
                target_layer = "L3"

            for eid, entry in self._entries[layer].items():
                i_val = entry.get("i_value", entry.get("i0_value", 0))
                # 失败条目禁止晋升 (S-03)
                if entry.get("needs_safety_check", False):
                    continue
                if i_val < forget_threshold:
                    forget_candidates.append({
                        "entry_id": eid,
                        "layer": layer,
                        "i_value": round(i_val, 2),
                        "reason": f"I值低于遗忘阈值{forget_threshold}"
                    })
                elif i_val >= promote_threshold:
                    promote_candidates.append({
                        "entry_id": eid,
                        "layer": layer,
                        "target_layer": target_layer,
                        "i_value": round(i_val, 2),
                        "reason": f"I值满足晋升阈值{promote_threshold}"
                    })

        # 上报候选清单给调度单元
        if self.bus and (forget_candidates or promote_candidates):
            self.bus.publish(
                topic="ag-mem-03.slot_result",
                source_module=self.module_id,
                data={
                    "action": "maintenance_result",
                    "slot_id": self.module_id,
                    "forget_candidates": forget_candidates,
                    "promote_candidates": promote_candidates
                },
                target_module="ag-mem-03"
            )

        self._log_event("MAINTENANCE_DONE", {
            "forget_count": len(forget_candidates),
            "promote_count": len(promote_candidates)
        })
        self.state = SlotState.IDLE

    def _is_sensitive_operation(self, experience_data: Dict[str, Any]) -> bool:
        # 检查 tools 字段
        tools = experience_data.get("tools", [])
        if isinstance(tools, list):
            for tool in tools:
                if isinstance(tool, str):
                    for op in self.SENSITIVE_OPS:
                        if op in tool.lower():
                            return True
        # 检查 operation 字段
        operation = experience_data.get("operation", "")
        if isinstance(operation, str):
            for op in self.SENSITIVE_OPS:
                if op in operation.lower():
                    return True
        return False

    # ====================== 回复工具 ======================
    def _reply_write_confirm(self, msg: Message, entry_id: str, layer: str,
                             duration_ms: float, success: bool, error: str = "",
                             safety_check: bool = False, l5_direct: bool = False):
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
                "error": error,
                "safety_check_required": safety_check,
                "l5_direct_write": l5_direct
            },
            target_module="ag-mem-03",
            correlation_id=msg.correlation_id
        )

    def _reply_query_result(self, msg: Message, matched: List[Dict], duration_ms: float):
        if not self.bus:
            return
        self.bus.publish(
            topic="ag-mem-03.slot_result",
            source_module=self.module_id,
            data={
                "matched_experiences": matched,
                "total_count": len(matched),
                "query_duration_ms": duration_ms
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
        self._entries.clear()
        self._layer_counts = {k: 0 for k in self._layer_counts}
        self._total_entries = 0
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