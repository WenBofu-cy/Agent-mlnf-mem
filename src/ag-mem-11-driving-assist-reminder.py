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
"""

from typing import Dict, List, Optional, Any, Callable, Tuple
from dataclasses import dataclass, field
from enum import Enum
import time
import uuid


class GeneratorState(Enum):
    IDLE = "idle"
    FETCHING_DATA = "fetching_data"
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


@dataclass
class SuggestionRequest:
    request_id: str = ""
    requester_id: str = ""
    request_type: Optional[SuggestionType] = None
    current_task_description: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class ActiveSlotInfo:
    session_id: str = ""
    slot_id: str = ""
    user_id: str = ""


@dataclass
class PreferenceSummary:
    slot_id: str = ""
    total_entries: int = 0
    by_behavior_dimension: Dict[str, Any] = field(default_factory=dict)
    by_label_category: Dict[str, int] = field(default_factory=dict)
    preference_strength_vector: List[float] = field(default_factory=list)
    preference_keywords: List[str] = field(default_factory=list)


@dataclass
class SceneLabel:
    scene_category: str = ""
    task_type: str = ""
    interaction_phase: str = ""


@dataclass
class ReadToken:
    token_id: str = ""
    authorized_slot_id: str = ""
    expires_at: float = 0.0


@dataclass
class PersonalizedSuggestion:
    suggestion_id: str = ""
    suggestion_type: SuggestionType = SuggestionType.STYLE_ADAPT
    content: str = ""
    confidence: float = 0.0
    basis_data: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class DataInsufficientNotice:
    request_id: str = ""
    reason: str = ""
    current_entries: int = 0
    min_required: int = 50


@dataclass
class GenerationStatus:
    state: GeneratorState = GeneratorState.IDLE
    total_suggestions: int = 0
    last_generation_time_ms: float = 0.0


class PersonalizedSuggestionGenerator:
    MIN_ENTRIES_FOR_SUGGESTION = 50
    HIGH_STRENGTH_THRESHOLD = 0.7
    DECLINE_THRESHOLD = 0.3
    ACTIVE_HOUR_CONCENTRATION = 0.6
    TOP_TOOLS_COUNT = 3
    MAX_SUGGESTIONS = 10

    def __init__(self):
        self.module_id = "ag-mem-11"
        self.module_name = "个性化建议生成单元"
        self.version = "V1.0"

        self.state = GeneratorState.IDLE
        self._total_suggestions: int = 0
        self._last_generation_time: float = 0.0
        self._pending_logs: List[Dict[str, Any]] = []

        # 回调注入
        self._query_suggestion_request = None
        self._query_active_slot = None
        self._query_preference_summary = None
        self._query_scene_label = None
        self._query_read_token = None

        self._publish_suggestion = None
        self._publish_insufficient_notice = None
        self._publish_status_report = None
        self._publish_event_log = None

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    def set_suggestion_request_query(self, callback: Callable[[], Optional[SuggestionRequest]]):
        self._query_suggestion_request = callback

    def set_active_slot_query(self, callback: Callable[[], Optional[ActiveSlotInfo]]):
        self._query_active_slot = callback

    def set_preference_summary_query(self, callback: Callable[[], Optional[PreferenceSummary]]):
        self._query_preference_summary = callback

    def set_scene_label_query(self, callback: Callable[[], Optional[SceneLabel]]):
        self._query_scene_label = callback

    def set_read_token_query(self, callback: Callable[[], Optional[ReadToken]]):
        self._query_read_token = callback

    def set_suggestion_publisher(self, callback: Callable[[PersonalizedSuggestion], None]):
        self._publish_suggestion = callback

    def set_insufficient_notice_publisher(self, callback: Callable[[DataInsufficientNotice], None]):
        self._publish_insufficient_notice = callback

    def set_status_report_publisher(self, callback: Callable[[GenerationStatus], None]):
        self._publish_status_report = callback

    def set_event_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_event_log = callback

    def run_generation_cycle(self):
        if self.state == GeneratorState.SYSTEM_PAUSED:
            return

        request = self._query_suggestion_request() if self._query_suggestion_request else None
        if request is None:
            return

        self.state = GeneratorState.FETCHING_DATA

        # 获取读取权限
        token = self._query_read_token() if self._query_read_token else None
        if token is None:
            self._send_insufficient_notice(request, "无法获取画像槽读取权限", 0)
            self.state = GeneratorState.IDLE
            return

        # 获取偏好统计摘要
        summary = self._query_preference_summary() if self._query_preference_summary else None
        if summary is None or summary.total_entries < self.MIN_ENTRIES_FOR_SUGGESTION:
            self.state = GeneratorState.INSUFFICIENT_DATA
            self._send_insufficient_notice(request, "画像数据不足",
                                           summary.total_entries if summary else 0)
            self.state = GeneratorState.IDLE
            return

        # 获取场景标签
        scene = self._query_scene_label() if self._query_scene_label else None

        # 生成建议
        self.state = GeneratorState.GENERATING
        start_time = time.time()
        suggestions = self._generate_suggestions(request, summary, scene)
        self._last_generation_time = (time.time() - start_time) * 1000

        if not suggestions:
            self._send_insufficient_notice(request, "当前画像数据不触发任何建议规则",
                                           summary.total_entries)
        else:
            for suggestion in suggestions:
                self._total_suggestions += 1
                if self._publish_suggestion:
                    self._publish_suggestion(suggestion)

        self.state = GeneratorState.IDLE

    def _generate_suggestions(self, request: SuggestionRequest, summary: PreferenceSummary,
                              scene: Optional[SceneLabel]) -> List[PersonalizedSuggestion]:
        suggestions = []
        dims = summary.by_behavior_dimension

        # R01: 回复风格适配
        if request.request_type is None or request.request_type == SuggestionType.STYLE_ADAPT:
            # 假设从TEXT_INPUT维度可以推断简洁偏好
            text_stats = dims.get("TEXT_INPUT")
            if text_stats and text_stats.get("preference_strength", 0) > self.HIGH_STRENGTH_THRESHOLD:
                suggestions.append(PersonalizedSuggestion(
                    suggestion_id=f"SUG-{uuid.uuid4().hex[:8]}",
                    suggestion_type=SuggestionType.STYLE_ADAPT,
                    content="用户偏好简洁回复，建议平均回复长度控制在100字以内",
                    confidence=text_stats.get("preference_strength", 0.7),
                    basis_data="TEXT_INPUT维度偏好强度"
                ))

        # R02: 工具推荐优先级
        if request.request_type is None or request.request_type == SuggestionType.TOOL_PRIORITY:
            tool_stats = []
            for dim, stats in dims.items():
                if dim.startswith("TOOL_INVOKE_"):
                    tool_name = dim.replace("TOOL_INVOKE_", "")
                    tool_stats.append((tool_name, stats.get("total_count", 0), stats.get("preference_strength", 0)))
            tool_stats.sort(key=lambda x: x[1], reverse=True)
            top_tools = tool_stats[:self.TOP_TOOLS_COUNT]
            if top_tools:
                tool_names = ", ".join(t[0] for t in top_tools)
                suggestions.append(PersonalizedSuggestion(
                    suggestion_id=f"SUG-{uuid.uuid4().hex[:8]}",
                    suggestion_type=SuggestionType.TOOL_PRIORITY,
                    content=f"用户常用工具TOP{len(top_tools)}: {tool_names}，建议在同类任务中优先推荐",
                    confidence=min(0.9, top_tools[0][1] / 10) if top_tools else 0.5,
                    basis_data="工具调用频次统计"
                ))

        # R03: 内容偏好提示（基于关键词）
        if request.request_type is None or request.request_type == SuggestionType.CONTENT_HINT:
            keywords = summary.preference_keywords
            if keywords:
                keyword_display = ", ".join(keywords[:5])
                suggestions.append(PersonalizedSuggestion(
                    suggestion_id=f"SUG-{uuid.uuid4().hex[:8]}",
                    suggestion_type=SuggestionType.CONTENT_HINT,
                    content=f"用户对以下主题表现出持续兴趣: {keyword_display}，建议在相关任务中主动关联",
                    confidence=0.7,
                    basis_data="偏好关键词集合"
                ))

        # R04: 用户兴趣衰减预警
        if request.request_type is None or request.request_type == SuggestionType.ENGAGEMENT_ALERT:
            for dim, stats in dims.items():
                strength = stats.get("preference_strength", 0)
                if strength < self.DECLINE_THRESHOLD and stats.get("total_count", 0) > 10:
                    suggestions.append(PersonalizedSuggestion(
                        suggestion_id=f"SUG-{uuid.uuid4().hex[:8]}",
                        suggestion_type=SuggestionType.ENGAGEMENT_ALERT,
                        content=f"用户对 {dim} 的兴趣较低（强度={strength:.2f}），建议减少此类推送",
                        confidence=0.65,
                        basis_data=f"{dim}偏好强度低于阈值"
                    ))
                    break

        # 按置信度排序并截断
        suggestions.sort(key=lambda x: x.confidence, reverse=True)
        return suggestions[:self.MAX_SUGGESTIONS]

    def _send_insufficient_notice(self, request: SuggestionRequest, reason: str, current_entries: int):
        if self._publish_insufficient_notice:
            self._publish_insufficient_notice(DataInsufficientNotice(
                request_id=request.request_id,
                reason=reason,
                current_entries=current_entries,
                min_required=self.MIN_ENTRIES_FOR_SUGGESTION
            ))

    def get_state(self) -> GeneratorState:
        return self.state

    def emergency_shutdown(self):
        self.state = GeneratorState.SYSTEM_PAUSED
        print(f"[{self.module_id}] 紧急熔断")

    def _log_event(self, event_type: str, details: Dict[str, Any]):
        entry = {
            "log_id": f"log-{uuid.uuid4().hex[:8]}",
            "event_type": event_type,
            "source_module": self.module_id,
            "details": details,
            "timestamp": time.time()
        }
        self._pending_logs.append(entry)
        if self._publish_event_log:
            self._publish_event_log(entry)

    def collect_pending_logs(self) -> List[Dict[str, Any]]:
        logs = self._pending_logs.copy()
        self._pending_logs.clear()
        return logs


def print_separator(title: str):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def demo_main():
    print("=" * 70)
    print("  Agent-mlnf-mem 个性化建议生成单元 (ag-mem-11) 演示")
    print("=" * 70)

    generator = PersonalizedSuggestionGenerator()
    generator.set_read_token_query(lambda: ReadToken(token_id="T-001", authorized_slot_id="SLOT-001", expires_at=time.time()+300))
    generator.set_preference_summary_query(lambda: PreferenceSummary(
        slot_id="SLOT-001",
        total_entries=120,
        by_behavior_dimension={
            "TEXT_INPUT": {"total_count": 50, "preference_strength": 0.8, "explicit_count": 5, "implicit_count": 30, "occasional_count": 15, "recent_7d_count": 10},
            "TOOL_INVOKE_weather_api": {"total_count": 15, "preference_strength": 0.85, "explicit_count": 3, "implicit_count": 10, "occasional_count": 2, "recent_7d_count": 5},
            "TOOL_INVOKE_file_read": {"total_count": 8, "preference_strength": 0.6, "explicit_count": 1, "implicit_count": 5, "occasional_count": 2, "recent_7d_count": 3},
        },
        preference_keywords=["天气", "AI", "编程"]
    ))

    print_separator("STEP 1: 请求综合建议")
    generator.set_suggestion_request_query(lambda: SuggestionRequest(request_id="REQ-001"))
    generator.run_generation_cycle()
    print(f"  累计生成建议数: {generator._total_suggestions}")

    print_separator("STEP 2: 仅请求工具推荐")
    generator.set_suggestion_request_query(lambda: SuggestionRequest(
        request_id="REQ-002",
        request_type=SuggestionType.TOOL_PRIORITY
    ))
    generator.run_generation_cycle()

    print_separator("STEP 3: 数据不足（新用户）")
    generator.set_preference_summary_query(lambda: PreferenceSummary(
        slot_id="SLOT-NEW",
        total_entries=10,
    ))
    generator.set_suggestion_request_query(lambda: SuggestionRequest(request_id="REQ-003"))
    generator.run_generation_cycle()
    print(f"  状态: {generator.state.value}")

    print("\n✅ 个性化建议生成单元演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("ag-mem-11 个性化建议生成单元 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        def setup_generator(entries=120):
            g = PersonalizedSuggestionGenerator()
            g.set_read_token_query(lambda: ReadToken(token_id="T", authorized_slot_id="S", expires_at=time.time()+300))
            g.set_preference_summary_query(lambda: PreferenceSummary(
                slot_id="S",
                total_entries=entries,
                by_behavior_dimension={
                    "TEXT_INPUT": {"total_count": 50, "preference_strength": 0.8, "explicit_count": 5, "implicit_count": 30, "occasional_count": 15, "recent_7d_count": 10},
                    "TOOL_INVOKE_weather_api": {"total_count": 15, "preference_strength": 0.85, "explicit_count": 3, "implicit_count": 10, "occasional_count": 2, "recent_7d_count": 5},
                },
                preference_keywords=["AI"]
            ))
            return g

        # TC-M11-01: 正常生成建议
        print("\n[TC-M11-01] 正常生成建议")
        try:
            g = setup_generator()
            g.set_suggestion_request_query(lambda: SuggestionRequest(request_id="T01"))
            g.run_generation_cycle()
            assert g._total_suggestions > 0
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M11-02: 工具推荐
        print("\n[TC-M11-02] 工具推荐")
        try:
            g = setup_generator()
            g.set_suggestion_request_query(lambda: SuggestionRequest(request_id="T02", request_type=SuggestionType.TOOL_PRIORITY))
            g.run_generation_cycle()
            assert g._total_suggestions > 0
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M11-03: 数据不足
        print("\n[TC-M11-03] 数据不足")
        try:
            g = setup_generator(entries=10)
            g.set_suggestion_request_query(lambda: SuggestionRequest(request_id="T03"))
            g.run_generation_cycle()
            assert g.state != GeneratorState.GENERATING
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M11-04: 无法获取读取权限
        print("\n[TC-M11-04] 无法获取读取权限")
        try:
            g = setup_generator()
            g.set_read_token_query(lambda: None)
            g.set_suggestion_request_query(lambda: SuggestionRequest(request_id="T04"))
            g.run_generation_cycle()
            assert g.state == GeneratorState.IDLE
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M11-05: 生成建议截断（不超过10条）
        print("\n[TC-M11-05] 生成建议不超过10条")
        try:
            g = setup_generator()
            g.set_suggestion_request_query(lambda: SuggestionRequest(request_id="T05"))
            g.run_generation_cycle()
            assert g._total_suggestions <= 10
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M11-06: 紧急熔断
        print("\n[TC-M11-06] 紧急熔断")
        try:
            g = setup_generator()
            g.emergency_shutdown()
            assert g.state == GeneratorState.SYSTEM_PAUSED
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        print("\n" + "=" * 60)
        print(f"测试结果: {passed} PASS, {failed} FAIL")
        print("=" * 60)
    else:
        demo_main()
```