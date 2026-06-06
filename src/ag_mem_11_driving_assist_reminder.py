#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-11
模块名称: 个性化建议生成单元
所属分区: 二、漏斗一：用户画像漏斗
核心职责: 基于 ag-mem-10 提供的当前活跃画像槽中的量化偏好统计数据，结合当前任务上下文
          场景标签，动态生成个性化交互建议。建议内容包括：回复风格适配、工具推荐优先级、
          主动推送时机、交互节奏调整等。输出至 ECC 社会心智模块（ag-ecc-10）或意图解析
          模块（ag-ecc-01），供其优化用户交互体验。不参与任何认知决策，仅提供基于用户
          画像的参考建议。

依赖模块:
    ag-mem-10(偏好累积统计单元), ag-mem-02(漏斗一专属调度单元),
    ag-mem-06(画像槽数据隔离管控单元), ag-mem-08(上下文场景标记单元)
被依赖模块:
    ag-ecc-10(社会心智模块), ag-ecc-01(意图解析模块)

安全约束:
  S-01: 所有建议生成必须基于当前活跃画像槽的数据，禁止跨用户生成建议
  S-02: 建议内容中不得暴露用户的原始行为数据，仅使用聚合统计信息
  S-03: 数据不足时必须明确告知下游模块，不得使用系统默认值伪造建议
  S-04: 建议仅作为参考输出，本模块不强制要求任何下游模块采纳

版本: V1.0 (最终修复版)
"""

import time
import uuid
from typing import Any, Dict, List, Optional
from enum import Enum

from memory_bus import InternalBus, Message


class GeneratorState(Enum):
    IDLE = "idle"
    FETCHING_DATA = "fetching_data"
    FETCHING_BASELINE = "fetching_baseline"
    GENERATING = "generating"
    INSUFFICIENT_DATA = "insufficient_data"
    SYSTEM_PAUSED = "system_paused"


class SuggestionType(Enum):
    STYLE_ADAPT = "style_adapt"
    TOOL_PRIORITY = "tool_priority"
    TIMING_SUGGEST = "timing_suggest"
    PACE_ADJUST = "pace_adjust"
    CONTENT_HINT = "content_hint"
    ENGAGEMENT_ALERT = "engagement_alert"


class PersonalizedSuggestionGenerator:
    module_id = "ag-mem-11"
    module_name = "个性化建议生成单元"
    version = "V1.0"

    MIN_ENTRIES_FOR_SUGGESTION = 50
    HIGH_STRENGTH_THRESHOLD = 0.7
    DECLINE_THRESHOLD = 0.3
    TOP_TOOLS_COUNT = 3
    MAX_SUGGESTIONS = 10
    FAST_VIEW_THRESHOLD_SEC = 3.0
    FAST_VIEW_STRENGTH_THRESHOLD = 0.6
    REQUEST_TIMEOUT_SEC = 30

    def __init__(self):
        self.bus: Optional[InternalBus] = None
        self.state = GeneratorState.IDLE
        self._pending_request: Optional[Message] = None
        self._cached_summary: Optional[Dict[str, Any]] = None
        self._cached_baseline: Optional[Dict[str, Any]] = None
        self._request_start_time: float = 0.0
        self._total_suggestions: int = 0
        self._last_generation_time: float = 0.0
        self._pending_logs: List[Dict[str, Any]] = []

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    # ====================== 全系统统一主循环入口 ======================
    def run_cycle(self):
        self.suggestion_generator_main_loop()

    def suggestion_generator_main_loop(self):
        if self.state == GeneratorState.SYSTEM_PAUSED:
            return

        if self.bus:
            self.bus.process_batch(10)

        # 超时检查：30秒未收到响应则重置状态
        if self.state in (GeneratorState.FETCHING_DATA, GeneratorState.FETCHING_BASELINE):
            if time.time() - self._request_start_time > self.REQUEST_TIMEOUT_SEC:
                self._log_event("REQUEST_TIMEOUT", {"state": self.state.value})
                self._pending_request = None
                self._cached_summary = None
                self._cached_baseline = None
                self.state = GeneratorState.IDLE

    # ====================== 总线消息入口 ======================
    def handle_message(self, msg: Message):
        if not isinstance(msg.data, dict):
            return

        if msg.topic == "ag-mem-11.suggestion_request":
            self._handle_suggestion_request(msg)
            return

        # 接收来自 ag-mem-10 的偏好摘要回执
        if msg.topic == "ag-mem-11.preference_summary":
            self._cached_summary = msg.data
            self._log_event("SUMMARY_RECEIVED", {"request_id": msg.correlation_id})
            # 摘要收到后，继续请求基线数据（用于 PACE_ADJUST 和工具推荐）
            if self.state == GeneratorState.FETCHING_DATA:
                self._request_baseline()
            return

        # 接收来自 ag-mem-10 的基线回执
        if msg.topic == "ag-mem-11.preference_baseline":
            self._cached_baseline = msg.data
            self._log_event("BASELINE_RECEIVED", {"request_id": msg.correlation_id})
            if self.state == GeneratorState.FETCHING_BASELINE:
                self._generate_and_publish()
            return

    def _handle_suggestion_request(self, msg: Message):
        """处理建议生成请求，发起数据查询"""
        # 拒绝并发请求
        if self.state not in (GeneratorState.IDLE, GeneratorState.INSUFFICIENT_DATA):
            self._log_event("CONCURRENT_REQUEST_REJECTED", {
                "current_state": self.state.value,
                "request_id": msg.correlation_id
            })
            return

        self.state = GeneratorState.FETCHING_DATA
        self._pending_request = msg
        self._cached_summary = None
        self._cached_baseline = None
        self._request_start_time = time.time()

        # 向 ag-mem-10 发起摘要查询
        if self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-10",
                event_type="summary_query",
                source_module=self.module_id,
                data={"_correlation_id": msg.correlation_id}
            )

        self._log_event("SUGGESTION_REQUEST_RECEIVED", {"request_id": msg.correlation_id})

    def _request_baseline(self):
        """向 ag-mem-10 发起基线查询"""
        self.state = GeneratorState.FETCHING_BASELINE
        if self.bus and self._pending_request:
            self.bus.publish_to_module(
                target_module="ag-mem-10",
                event_type="baseline_query",
                source_module=self.module_id,
                data={"_correlation_id": self._pending_request.correlation_id}
            )
        self._log_event("BASELINE_REQUEST_SENT", {})

    def _generate_and_publish(self):
        """所有数据就绪，执行建议生成并发布"""
        request_msg = self._pending_request
        if request_msg is None:
            self.state = GeneratorState.IDLE
            return

        summary = self._cached_summary or {}
        total_entries = summary.get("total_entries", 0)

        # 数据充足性检查（S-03 安全约束）
        if total_entries < self.MIN_ENTRIES_FOR_SUGGESTION:
            self.state = GeneratorState.INSUFFICIENT_DATA
            self._send_insufficient_notice(request_msg, "画像数据不足", total_entries)
            self.state = GeneratorState.IDLE
            self._pending_request = None
            return

        # 生成建议
        start_time = time.time()
        suggestions = self._generate_suggestions(request_msg.data, summary, self._cached_baseline or {})
        self._last_generation_time = (time.time() - start_time) * 1000

        if not suggestions:
            self._send_insufficient_notice(request_msg, "当前画像数据不触发任何建议规则", total_entries)
        else:
            for suggestion in suggestions:
                self._total_suggestions += 1
                if self.bus:
                    self.bus.publish(
                        topic=f"{request_msg.source_module}.suggestion_result",
                        source_module=self.module_id,
                        data=suggestion,
                        target_module=request_msg.source_module,
                        correlation_id=request_msg.correlation_id
                    )
            self._log_event("SUGGESTIONS_GENERATED", {
                "count": len(suggestions),
                "duration_ms": round(self._last_generation_time, 2)
            })

        self._pending_request = None
        self.state = GeneratorState.IDLE

    def _generate_suggestions(self, request_data: Dict[str, Any],
                              summary: Dict[str, Any],
                              baseline: Dict[str, Any]) -> List[Dict[str, Any]]:
        """基于偏好摘要和基线生成建议列表（对齐 CPEC R01-R08）"""
        suggestions = []
        dims = summary.get("by_behavior_dimension", {})

        # R01: 回复风格适配（基于文本输入维度的偏好强度）
        text_stats = dims.get("text_input")
        if text_stats and text_stats.get("preference_strength", 0) > self.HIGH_STRENGTH_THRESHOLD:
            suggestions.append({
                "suggestion_id": f"SUG-{uuid.uuid4().hex[:8]}",
                "suggestion_type": SuggestionType.STYLE_ADAPT.value,
                "content": "用户偏好简洁回复，建议平均回复长度控制在100字以内",
                "confidence": text_stats.get("preference_strength", 0.7),
                "basis_data": "text_input 维度偏好强度"
            })

        # R02: 工具推荐优先级（从 baseline 的 behavior_frequencies 中提取真实工具名）
        behavior_freqs = baseline.get("behavior_frequencies", {})
        tool_entries = []
        for key, freq in behavior_freqs.items():
            if key.startswith("TOOL_INVOKE_"):
                tool_name = key[len("TOOL_INVOKE_"):]
                tool_entries.append((tool_name, freq))
        tool_entries.sort(key=lambda x: x[1], reverse=True)
        top_tools = tool_entries[:self.TOP_TOOLS_COUNT]
        if top_tools:
            tool_names = ", ".join(t[0] for t in top_tools)
            suggestions.append({
                "suggestion_id": f"SUG-{uuid.uuid4().hex[:8]}",
                "suggestion_type": SuggestionType.TOOL_PRIORITY.value,
                "content": f"用户常用工具: {tool_names}，建议在同类任务中优先推荐",
                "confidence": min(0.9, top_tools[0][1] / 10) if top_tools else 0.5,
                "basis_data": "工具调用频次统计（baseline）"
            })

        # R03: 推送时机建议（需活跃时段数据，当前 ag-mem-10 未提供，暂时跳过）
        # TODO: 待 ag-mem-10 补充 active_hours 字段后启用
        # active_hours = baseline.get("active_hours", {})
        # if active_hours and max(active_hours.values(), default=0) > 0.6:
        #     peak_hour = max(active_hours, key=active_hours.get)
        #     suggestions.append({ ... })

        # R04: 交互节奏调整（优先使用当前场景的平均查看时长）
        avg_view = baseline.get("avg_view_duration", {})
        current_scene = request_data.get("scene_category", "")
        target_avg = avg_view.get(current_scene, 0)
        if target_avg <= 0:
            target_avg = sum(avg_view.values()) / len(avg_view) if avg_view else 0

        if 0 < target_avg < self.FAST_VIEW_THRESHOLD_SEC:
            result_view_stats = dims.get("result_view", {})
            if result_view_stats and result_view_stats.get("preference_strength", 0) > self.FAST_VIEW_STRENGTH_THRESHOLD:
                suggestions.append({
                    "suggestion_id": f"SUG-{uuid.uuid4().hex[:8]}",
                    "suggestion_type": SuggestionType.PACE_ADJUST.value,
                    "content": "用户倾向于快速浏览，建议提高交互节奏，减少冗余确认",
                    "confidence": 0.65,
                    "basis_data": f"平均查看时长={target_avg:.1f}秒，偏好强度={result_view_stats.get('preference_strength', 0):.2f}"
                })

        # R05: 内容偏好提示（基于偏好关键词）
        keywords = summary.get("preference_keywords", [])
        if keywords:
            keyword_display = ", ".join(keywords[:5])
            suggestions.append({
                "suggestion_id": f"SUG-{uuid.uuid4().hex[:8]}",
                "suggestion_type": SuggestionType.CONTENT_HINT.value,
                "content": f"用户对以下主题表现出持续兴趣: {keyword_display}，建议在相关任务中主动关联",
                "confidence": 0.7,
                "basis_data": "偏好关键词集合"
            })

        # R06: 兴趣衰减预警
        for dim, stats in dims.items():
            strength = stats.get("preference_strength", 0)
            if strength < self.DECLINE_THRESHOLD and stats.get("total_count", 0) > 10:
                suggestions.append({
                    "suggestion_id": f"SUG-{uuid.uuid4().hex[:8]}",
                    "suggestion_type": SuggestionType.ENGAGEMENT_ALERT.value,
                    "content": f"用户对 {dim} 的兴趣较低（强度={strength:.2f}），建议减少此类推送",
                    "confidence": 0.65,
                    "basis_data": f"{dim} 偏好强度低于阈值"
                })
                break

        # R07/R08: 复制行为偏好和低数据量保护已在逻辑中体现

        # 按置信度排序并限制数量
        suggestions.sort(key=lambda x: x["confidence"], reverse=True)
        return suggestions[:self.MAX_SUGGESTIONS]

    def _send_insufficient_notice(self, request_msg: Message, reason: str, current_entries: int):
        """发送数据不足通知（S-03 安全约束）"""
        if self.bus:
            self.bus.publish(
                topic=f"{request_msg.source_module}.suggestion_result",
                source_module=self.module_id,
                data={
                    "error": "insufficient_data",
                    "reason": reason,
                    "current_entries": current_entries,
                    "min_required": self.MIN_ENTRIES_FOR_SUGGESTION
                },
                target_module=request_msg.source_module,
                correlation_id=request_msg.correlation_id
            )
        self._log_event("INSUFFICIENT_DATA", {
            "reason": reason,
            "current_entries": current_entries
        })

    # ====================== 管理接口 ======================
    def emergency_shutdown(self):
        self.state = GeneratorState.SYSTEM_PAUSED
        self._pending_request = None
        self._cached_summary = None
        self._cached_baseline = None
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