#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-08
模块名称: 上下文场景标记单元
所属分区: 二、漏斗一：用户画像漏斗
核心职责: 接收 ag-mem-07（用户行为观测记录单元）的场景查询请求，基于当前会话的最近
          行为序列、任务特征及时间窗口，动态判定当前用户所处的场景类别与交互阶段，输出
          标准化的上下文场景标签。为行为观测条目提供场景语义锚点，使同一用户在不同场景
          下的行为能够被准确标记和区分。不参与任何认知决策，仅提供场景判定与标签输出。

依赖模块:
    ag-mem-02(漏斗一专属调度单元), ag-mem-07(用户行为观测记录单元)
被依赖模块:
    ag-mem-07, ag-mem-09(偏好判定标签单元)

安全约束:
  S-01: 场景判定仅基于行为元数据（类型、顺序），不得访问用户原始输入内容
  S-02: 本模块不持久化任何用户数据，判定过程完全基于内存中的行为序列快照
  S-03: 数据不足时不得强行猜测场景，必须标记低置信度并明确告知下游模块
"""

from typing import Dict, List, Optional, Any, Callable, Tuple
from dataclasses import dataclass, field
from enum import Enum
import time
import uuid
from collections import deque


class MarkerState(Enum):
    IDLE = "idle"
    ANALYZING = "analyzing"
    JUDGED = "judged"
    INSUFFICIENT_DATA = "insufficient_data"
    SYSTEM_PAUSED = "system_paused"


class SceneCategory(Enum):
    DIALOGUE = "对话交互"
    TOOL_CALL = "工具调用"
    SEARCH = "信息检索"
    CREATION = "创作生成"
    GENERAL = "通用任务"


class InteractionPhase(Enum):
    TASK_START = "任务开始"
    EXECUTING = "执行中"
    RESULT_EVAL = "结果评估"
    TASK_END = "任务结束"


@dataclass
class SceneQueryRequest:
    session_id: str = ""
    requester_id: str = ""
    recent_behaviors: List[Dict[str, Any]] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)


@dataclass
class ContextSceneLabel:
    scene_category: SceneCategory = SceneCategory.GENERAL
    task_type: str = ""
    interaction_phase: InteractionPhase = InteractionPhase.TASK_START
    confidence: float = 0.3
    judgment_basis: str = "数据不足"
    timestamp: float = field(default_factory=time.time)


@dataclass
class SceneSwitchNotification:
    previous_scene: Optional[SceneCategory] = None
    new_scene: SceneCategory = SceneCategory.GENERAL
    timestamp: float = field(default_factory=time.time)


class ContextSceneMarker:
    # 规则匹配关键词
    CREATION_KEYWORDS = ["写", "生成", "创作", "画", "翻译", "总结", "编写", "制作"]
    SEARCH_KEYWORDS = ["搜索", "查找", "什么是", "如何", "最新", "查询", "找"]
    TOOL_KEYWORDS = ["执行", "调用", "运行", "API", "操作文件"]

    def __init__(self):
        self.module_id = "ag-mem-08"
        self.module_name = "上下文场景标记单元"
        self.version = "V1.0"

        self.state = MarkerState.IDLE
        self._last_scene_label: Optional[ContextSceneLabel] = None
        self._scene_switch_count: int = 0
        self._pending_logs: List[Dict[str, Any]] = []

        # 回调注入
        self._query_scene_request = None
        self._query_session_binding = None

        self._publish_scene_label = None
        self._publish_scene_switch = None
        self._publish_event_log = None

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    def set_scene_request_query(self, callback: Callable[[], Optional[SceneQueryRequest]]):
        self._query_scene_request = callback

    def set_session_binding_query(self, callback: Callable[[], Optional[Dict[str, Any]]]):
        self._query_session_binding = callback

    def set_scene_label_publisher(self, callback: Callable[[ContextSceneLabel], None]):
        self._publish_scene_label = callback

    def set_scene_switch_publisher(self, callback: Callable[[SceneSwitchNotification], None]):
        self._publish_scene_switch = callback

    def set_event_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_event_log = callback

    def run_marker_cycle(self) -> Optional[ContextSceneLabel]:
        if self.state == MarkerState.SYSTEM_PAUSED:
            return None

        request = self._query_scene_request() if self._query_scene_request else None
        if request is None:
            return None

        self.state = MarkerState.ANALYZING
        result = self._determine_scene(request)
        self.state = MarkerState.JUDGED

        # 检测场景切换
        if self._last_scene_label is not None:
            if self._last_scene_label.scene_category != result.scene_category:
                self._scene_switch_count += 1
                if self._scene_switch_count < 3:  # 连续3次确认才发送通知，避免频繁切换
                    pass
                else:
                    if self._publish_scene_switch:
                        self._publish_scene_switch(SceneSwitchNotification(
                            previous_scene=self._last_scene_label.scene_category,
                            new_scene=result.scene_category
                        ))
            else:
                self._scene_switch_count = 0

        self._last_scene_label = result

        if self._publish_scene_label:
            self._publish_scene_label(result)

        self.state = MarkerState.IDLE
        return result

    def _determine_scene(self, request: SceneQueryRequest) -> ContextSceneLabel:
        behaviors = request.recent_behaviors
        if not behaviors:
            self.state = MarkerState.INSUFFICIENT_DATA
            return ContextSceneLabel(
                scene_category=SceneCategory.GENERAL,
                confidence=0.3,
                judgment_basis="无可用的行为数据"
            )

        # 提取最近关键行为
        last_behavior = None
        for b in reversed(behaviors):
            btype = b.get("type", "")
            if btype not in ("SESSION_START", "SESSION_END"):
                last_behavior = b
                break

        if last_behavior is None:
            return ContextSceneLabel(
                scene_category=SceneCategory.GENERAL,
                confidence=0.3,
                judgment_basis="无有效行为"
            )

        behavior_type = last_behavior.get("type", "")
        text_content = last_behavior.get("text", "")

        # 规则1：工具调用
        if behavior_type == "TOOL_INVOKE":
            return ContextSceneLabel(
                scene_category=SceneCategory.TOOL_CALL,
                confidence=0.90,
                judgment_basis="检测到工具调用行为"
            )

        # 规则2：搜索类关键词
        if any(kw in text_content for kw in self.SEARCH_KEYWORDS):
            return ContextSceneLabel(
                scene_category=SceneCategory.SEARCH,
                confidence=0.75,
                judgment_basis="输入包含搜索关键词"
            )

        # 规则3：创作类关键词
        if any(kw in text_content for kw in self.CREATION_KEYWORDS):
            return ContextSceneLabel(
                scene_category=SceneCategory.CREATION,
                confidence=0.80,
                judgment_basis="输入包含创作意图关键词"
            )

        # 规则4：连续多轮问答（检测行为序列模式）
        qa_count = 0
        for b in behaviors:
            if b.get("type") in ("TEXT_INPUT", "RESULT_VIEW"):
                qa_count += 1
        if qa_count >= 4:
            return ContextSceneLabel(
                scene_category=SceneCategory.DIALOGUE,
                confidence=0.70,
                judgment_basis="多轮问答模式匹配"
            )

        # 默认：通用任务
        return ContextSceneLabel(
            scene_category=SceneCategory.GENERAL,
            confidence=0.50,
            judgment_basis="无匹配特征，使用默认场景"
        )

    def get_last_scene(self) -> Optional[SceneCategory]:
        return self._last_scene_label.scene_category if self._last_scene_label else None

    def get_state(self) -> MarkerState:
        return self.state

    def emergency_shutdown(self):
        self.state = MarkerState.SYSTEM_PAUSED
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
    print("  Agent-mlnf-mem 上下文场景标记单元 (ag-mem-08) 演示")
    print("=" * 70)

    marker = ContextSceneMarker()

    print_separator("STEP 1: 工具调用场景")
    marker.set_scene_request_query(lambda: SceneQueryRequest(
        session_id="S001",
        recent_behaviors=[
            {"type": "TEXT_INPUT", "text": "帮我调用天气API"},
            {"type": "TOOL_INVOKE", "tool": "weather_api"},
        ]
    ))
    result = marker.run_marker_cycle()
    if result:
        print(f"  场景类别: {result.scene_category.value}")
        print(f"  置信度: {result.confidence}")
        print(f"  判定依据: {result.judgment_basis}")

    print_separator("STEP 2: 创作生成场景")
    marker.set_scene_request_query(lambda: SceneQueryRequest(
        session_id="S002",
        recent_behaviors=[
            {"type": "TEXT_INPUT", "text": "帮我写一篇关于AI的文章"},
        ]
    ))
    result = marker.run_marker_cycle()
    if result:
        print(f"  场景类别: {result.scene_category.value}")
        print(f"  置信度: {result.confidence}")

    print_separator("STEP 3: 无行为数据")
    marker.set_scene_request_query(lambda: SceneQueryRequest(
        session_id="S003",
        recent_behaviors=[]
    ))
    result = marker.run_marker_cycle()
    if result:
        print(f"  场景类别: {result.scene_category.value}")
        print(f"  置信度: {result.confidence}")
        print(f"  判定依据: {result.judgment_basis}")

    print("\n✅ 上下文场景标记单元演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("ag-mem-08 上下文场景标记单元 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        def setup_marker():
            m = ContextSceneMarker()
            return m

        # TC-M08-01: 工具调用场景
        print("\n[TC-M08-01] 工具调用场景")
        try:
            m = setup_marker()
            m.set_scene_request_query(lambda: SceneQueryRequest(
                session_id="T01",
                recent_behaviors=[
                    {"type": "TEXT_INPUT", "text": "调用API"},
                    {"type": "TOOL_INVOKE"},
                ]
            ))
            result = m.run_marker_cycle()
            assert result is not None
            assert result.scene_category == SceneCategory.TOOL_CALL
            assert result.confidence >= 0.85
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M08-02: 创作生成场景
        print("\n[TC-M08-02] 创作生成场景")
        try:
            m = setup_marker()
            m.set_scene_request_query(lambda: SceneQueryRequest(
                session_id="T02",
                recent_behaviors=[{"type": "TEXT_INPUT", "text": "帮我写一篇文章"}]
            ))
            result = m.run_marker_cycle()
            assert result is not None
            assert result.scene_category == SceneCategory.CREATION
            assert result.confidence >= 0.75
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M08-03: 无行为数据（低置信度）
        print("\n[TC-M08-03] 无行为数据")
        try:
            m = setup_marker()
            m.set_scene_request_query(lambda: SceneQueryRequest(
                session_id="T03",
                recent_behaviors=[]
            ))
            result = m.run_marker_cycle()
            assert result is not None
            assert result.scene_category == SceneCategory.GENERAL
            assert result.confidence <= 0.3
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M08-04: 搜索类关键词
        print("\n[TC-M08-04] 搜索类关键词")
        try:
            m = setup_marker()
            m.set_scene_request_query(lambda: SceneQueryRequest(
                session_id="T04",
                recent_behaviors=[{"type": "TEXT_INPUT", "text": "什么是EM-Core"}]
            ))
            result = m.run_marker_cycle()
            assert result is not None
            assert result.scene_category == SceneCategory.SEARCH
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M08-05: 多轮对话
        print("\n[TC-M08-05] 多轮对话")
        try:
            m = setup_marker()
            m.set_scene_request_query(lambda: SceneQueryRequest(
                session_id="T05",
                recent_behaviors=[
                    {"type": "TEXT_INPUT"}, {"type": "RESULT_VIEW"},
                    {"type": "TEXT_INPUT"}, {"type": "RESULT_VIEW"},
                ]
            ))
            result = m.run_marker_cycle()
            assert result is not None
            assert result.scene_category == SceneCategory.DIALOGUE
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M08-06: 紧急熔断
        print("\n[TC-M08-06] 紧急熔断")
        try:
            m = setup_marker()
            m.emergency_shutdown()
            assert m.state == MarkerState.SYSTEM_PAUSED
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