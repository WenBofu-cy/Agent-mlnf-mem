#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-14
模块名称: 任务场景判定与分槽路由单元
所属分区: 三、漏斗二：任务经验漏斗 / 场景分槽管理
核心职责: 接收 ag-mem-03 下发的场景判定请求，基于任务描述、用户历史行为特征与当前上下文，
          判定当前任务所属的场景类别（对话交互、工具调用、信息检索、创作生成、通用任务），
          并输出目标分槽编号。支持多场景匹配时的置信度排序与关联标记。为漏斗二的经验路由
          提供唯一的场景分类依据。不参与认知决策，仅执行场景判定与分槽推荐。

依赖模块:
    ag-mem-03(漏斗二专属调度单元), ag-mem-10(偏好累积统计单元)
被依赖模块:
    ag-mem-03, ag-mem-15~19(各场景分槽)

安全约束:
  S-01: 场景判定仅基于任务描述的元数据特征，不得解析用户原始输入中的敏感内容
  S-02: 用户偏好数据仅用于置信度修正，不得作为场景判定的唯一依据
  S-03: 当所有场景置信度均低于阈值时，必须返回通用任务，不得随机选择场景
  S-04: 本模块不持久化任何用户数据，判定过程完全基于内存
"""

from typing import Dict, List, Optional, Any, Callable, Tuple
from dataclasses import dataclass, field
from enum import Enum
import time
import uuid


class RouterState(Enum):
    IDLE = "idle"
    FEATURE_EXTRACT = "feature_extract"
    RULE_MATCH = "rule_match"
    RESULT_OUTPUT = "result_output"
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


@dataclass
class SceneJudgmentRequest:
    request_id: str = ""
    task_description: str = ""
    context: Dict[str, Any] = field(default_factory=dict)
    user_id: str = ""
    recent_behaviors: List[Dict[str, Any]] = field(default_factory=list)
    need_user_preference_assist: bool = False
    timestamp: float = field(default_factory=time.time)


@dataclass
class UserPreferenceSummary:
    user_id: str = ""
    preference_keywords: List[str] = field(default_factory=list)
    high_freq_tools: List[str] = field(default_factory=list)
    scene_distribution: Dict[str, float] = field(default_factory=dict)


@dataclass
class SceneJudgmentResult:
    request_id: str = ""
    primary_scene: SceneCategory = SceneCategory.GENERAL
    confidence: float = 0.3
    target_slot_id: str = ""
    secondary_scenes: List[Dict[str, Any]] = field(default_factory=list)
    judgment_basis: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class RouterStatus:
    state: RouterState = RouterState.IDLE
    recent_judgment_count: int = 0
    scene_hit_distribution: Dict[str, int] = field(default_factory=dict)
    avg_judgment_time_ms: float = 0.0


class SceneJudgmentRouter:
    # 场景判定关键词
    CREATION_KEYWORDS = ["写", "生成", "创作", "画", "翻译", "总结", "编写", "制作"]
    SEARCH_KEYWORDS = ["搜索", "查找", "什么是", "如何", "最新", "查询", "找"]
    TOOL_KEYWORDS = ["执行", "调用", "运行", "API", "操作文件"]
    DIALOGUE_KEYWORDS = ["聊天", "问候", "闲聊", "你好"]

    # 场景默认置信度
    DEFAULT_CONFIDENCE = {
        SceneCategory.DIALOGUE: 0.75,
        SceneCategory.TOOL_CALL: 0.85,
        SceneCategory.SEARCH: 0.80,
        SceneCategory.CREATION: 0.80,
        SceneCategory.GENERAL: 0.50,
    }

    # 多场景匹配阈值（置信度差值小于此值则作为次选场景）
    SECONDARY_SCENE_THRESHOLD = 0.1
    # 最低置信度阈值
    MIN_CONFIDENCE_THRESHOLD = 0.3
    # 用户偏好修正幅度
    USER_PREFERENCE_BOOST = 0.1
    USER_PREFERENCE_PENALTY = 0.05
    # 高频场景占比阈值
    HIGH_FREQ_SCENE_RATIO = 0.5
    # 状态上报间隔（判定次数）
    REPORT_INTERVAL_COUNT = 30

    def __init__(self):
        self.module_id = "ag-mem-14"
        self.module_name = "任务场景判定与分槽路由单元"
        self.version = "V1.0"

        self.state = RouterState.IDLE
        self._judgment_count: int = 0
        self._scene_hit_count: Dict[SceneCategory, int] = {s: 0 for s in SceneCategory}
        self._total_judgment_time: float = 0.0
        self._last_report_count: int = 0
        self._pending_logs: List[Dict[str, Any]] = []

        # 回调注入
        self._query_scene_request = None
        self._query_user_preference = None

        self._publish_judgment_result = None
        self._publish_status_report = None
        self._publish_event_log = None

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    def set_scene_request_query(self, callback: Callable[[], Optional[SceneJudgmentRequest]]):
        self._query_scene_request = callback

    def set_user_preference_query(self, callback: Callable[[], Optional[UserPreferenceSummary]]):
        self._query_user_preference = callback

    def set_judgment_result_publisher(self, callback: Callable[[SceneJudgmentResult], None]):
        self._publish_judgment_result = callback

    def set_status_report_publisher(self, callback: Callable[[RouterStatus], None]):
        self._publish_status_report = callback

    def set_event_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_event_log = callback

    def run_judgment_cycle(self) -> Optional[SceneJudgmentResult]:
        if self.state == RouterState.SYSTEM_PAUSED:
            return None

        request = self._query_scene_request() if self._query_scene_request else None
        if request is None:
            return None

        start_time = time.time()
        self.state = RouterState.FEATURE_EXTRACT

        # 提取特征
        keywords = self._extract_keywords(request.task_description)
        intent_verbs = self._extract_intent_verbs(request.task_description)
        has_tool_name = self._detect_tool_name(request.task_description)

        # 可选：查询用户历史偏好
        user_preference = None
        use_preference_boost = False
        if request.need_user_preference_assist:
            user_preference = self._query_user_preference() if self._query_user_preference else None
            if user_preference is not None:
                use_preference_boost = True

        # 规则匹配
        self.state = RouterState.RULE_MATCH
        candidates = self._match_scenes(keywords, intent_verbs, has_tool_name, request.recent_behaviors)

        # 用户偏好修正
        if use_preference_boost and user_preference:
            self._apply_preference_boost(candidates, user_preference)

        # 排序
        candidates.sort(key=lambda x: x["confidence"], reverse=True)

        # 生成结果
        self.state = RouterState.RESULT_OUTPUT

        if not candidates or candidates[0]["confidence"] < self.MIN_CONFIDENCE_THRESHOLD:
            result = SceneJudgmentResult(
                request_id=request.request_id,
                primary_scene=SceneCategory.GENERAL,
                confidence=0.3,
                target_slot_id=SCENE_TO_SLOT_MAP[SceneCategory.GENERAL],
                judgment_basis="无法匹配任何场景规则"
            )
        else:
            primary = candidates[0]
            secondary = []
            for c in candidates[1:]:
                if primary["confidence"] - c["confidence"] < self.SECONDARY_SCENE_THRESHOLD:
                    secondary.append(c)
                else:
                    break

            result = SceneJudgmentResult(
                request_id=request.request_id,
                primary_scene=primary["scene"],
                confidence=primary["confidence"],
                target_slot_id=primary["slot_id"],
                secondary_scenes=secondary[:2],  # 最多2个次选
                judgment_basis=primary.get("basis", "规则匹配")
            )

        # 更新统计
        elapsed = (time.time() - start_time) * 1000
        self._judgment_count += 1
        self._scene_hit_count[result.primary_scene] += 1
        self._total_judgment_time += elapsed

        if self._publish_judgment_result:
            self._publish_judgment_result(result)

        # 周期性状态上报
        if self._judgment_count - self._last_report_count >= self.REPORT_INTERVAL_COUNT:
            self._last_report_count = self._judgment_count
            self._publish_status()

        self.state = RouterState.IDLE
        return result

    def _extract_keywords(self, text: str) -> List[str]:
        if not text:
            return []
        import re
        return re.findall(r'[\w]+', text)

    def _extract_intent_verbs(self, text: str) -> List[str]:
        verbs = []
        for kw in self.CREATION_KEYWORDS + self.SEARCH_KEYWORDS + self.TOOL_KEYWORDS:
            if kw in text:
                verbs.append(kw)
        return verbs

    def _detect_tool_name(self, text: str) -> bool:
        return any(kw in text for kw in self.TOOL_KEYWORDS)

    def _match_scenes(self, keywords: List[str], intent_verbs: List[str],
                       has_tool_name: bool, behaviors: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        candidates = []

        for scene in SceneCategory:
            if scene == SceneCategory.GENERAL:
                continue

            score = 0
            basis_parts = []
            scene_keywords = self._get_scene_keywords(scene)

            # 关键词匹配
            matched = [kw for kw in keywords if kw in scene_keywords]
            score += len(matched)
            if matched:
                basis_parts.append(f"关键词匹配: {matched}")

            # 意图动词匹配
            verb_match = [v for v in intent_verbs if v in scene_keywords]
            score += len(verb_match) * 2
            if verb_match:
                basis_parts.append("意图动词匹配")

            # 工具名称检测
            if scene == SceneCategory.TOOL_CALL and has_tool_name:
                score += 3
                basis_parts.append("检测到工具名称")

            if score > 0:
                confidence = min(self.DEFAULT_CONFIDENCE[scene] + score * 0.05, 1.0)
                candidates.append({
                    "scene": scene,
                    "slot_id": SCENE_TO_SLOT_MAP[scene],
                    "confidence": confidence,
                    "basis": ", ".join(basis_parts)
                })

        # 检测多轮问答模式
        if self._detect_multi_turn_qa(behaviors):
            candidates.append({
                "scene": SceneCategory.DIALOGUE,
                "slot_id": SCENE_TO_SLOT_MAP[SceneCategory.DIALOGUE],
                "confidence": 0.70,
                "basis": "多轮问答模式匹配"
            })

        return candidates

    def _get_scene_keywords(self, scene: SceneCategory) -> List[str]:
        if scene == SceneCategory.CREATION:
            return self.CREATION_KEYWORDS
        elif scene == SceneCategory.SEARCH:
            return self.SEARCH_KEYWORDS
        elif scene == SceneCategory.TOOL_CALL:
            return self.TOOL_KEYWORDS
        elif scene == SceneCategory.DIALOGUE:
            return self.DIALOGUE_KEYWORDS
        return []

    def _detect_multi_turn_qa(self, behaviors: List[Dict[str, Any]]) -> bool:
        if not behaviors:
            return False
        qa_count = 0
        for b in behaviors:
            if b.get("type") in ("TEXT_INPUT", "RESULT_VIEW"):
                qa_count += 1
        return qa_count >= 4

    def _apply_preference_boost(self, candidates: List[Dict[str, Any]], preference: UserPreferenceSummary):
        for candidate in candidates:
            scene_name = candidate["scene"].value
            scene_ratio = preference.scene_distribution.get(scene_name, 0.0)
            if scene_ratio > self.HIGH_FREQ_SCENE_RATIO:
                candidate["confidence"] += self.USER_PREFERENCE_BOOST
                candidate["basis"] += ", 用户高频场景+0.1"
            elif scene_ratio == 0.0:
                candidate["confidence"] -= self.USER_PREFERENCE_PENALTY
                candidate["basis"] += ", 用户历史无此场景-0.05"

    def _publish_status(self):
        if self._publish_status_report:
            self._publish_status_report(RouterStatus(
                state=self.state,
                recent_judgment_count=self._judgment_count,
                scene_hit_distribution={k.value: v for k, v in self._scene_hit_count.items()},
                avg_judgment_time_ms=self._total_judgment_time / max(self._judgment_count, 1)
            ))

    def get_state(self) -> RouterState:
        return self.state

    def emergency_shutdown(self):
        self.state = RouterState.SYSTEM_PAUSED
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
    print("  Agent-mlnf-mem 任务场景判定与分槽路由单元 (ag-mem-14) 演示")
    print("=" * 70)

    router = SceneJudgmentRouter()

    print_separator("STEP 1: 工具调用场景")
    router.set_scene_request_query(lambda: SceneJudgmentRequest(
        request_id="REQ-001",
        task_description="帮我调用天气API查询北京天气"
    ))
    result = router.run_judgment_cycle()
    if result:
        print(f"  主场景: {result.primary_scene.value}")
        print(f"  置信度: {result.confidence:.2f}")
        print(f"  目标分槽: {result.target_slot_id}")
        print(f"  判定依据: {result.judgment_basis}")

    print_separator("STEP 2: 创作生成场景")
    router.set_scene_request_query(lambda: SceneJudgmentRequest(
        request_id="REQ-002",
        task_description="帮我写一篇关于AI的短文"
    ))
    result = router.run_judgment_cycle()
    if result:
        print(f"  主场景: {result.primary_scene.value}")
        print(f"  置信度: {result.confidence:.2f}")

    print_separator("STEP 3: 无特征输入（回退到通用任务）")
    router.set_scene_request_query(lambda: SceneJudgmentRequest(
        request_id="REQ-003",
        task_description="嗯"
    ))
    result = router.run_judgment_cycle()
    if result:
        print(f"  主场景: {result.primary_scene.value}")
        print(f"  置信度: {result.confidence:.2f}")

    print("\n✅ 任务场景判定与分槽路由单元演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("ag-mem-14 任务场景判定与分槽路由单元 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        def setup_router():
            return SceneJudgmentRouter()

        # TC-M14-01: 工具调用场景
        print("\n[TC-M14-01] 工具调用场景")
        try:
            r = setup_router()
            r.set_scene_request_query(lambda: SceneJudgmentRequest(
                request_id="T01", task_description="调用API查询天气"
            ))
            result = r.run_judgment_cycle()
            assert result is not None
            assert result.primary_scene == SceneCategory.TOOL_CALL
            assert result.confidence >= 0.85
            assert result.target_slot_id == "ag-mem-16"
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M14-02: 创作生成场景
        print("\n[TC-M14-02] 创作生成场景")
        try:
            r = setup_router()
            r.set_scene_request_query(lambda: SceneJudgmentRequest(
                request_id="T02", task_description="写一篇关于AI的文章"
            ))
            result = r.run_judgment_cycle()
            assert result is not None
            assert result.primary_scene == SceneCategory.CREATION
            assert result.confidence >= 0.80
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M14-03: 无特征回退通用任务
        print("\n[TC-M14-03] 无特征回退通用任务")
        try:
            r = setup_router()
            r.set_scene_request_query(lambda: SceneJudgmentRequest(
                request_id="T03", task_description="嗯"
            ))
            result = r.run_judgment_cycle()
            assert result is not None
            assert result.primary_scene == SceneCategory.GENERAL
            assert result.confidence <= 0.3
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M14-04: 搜索场景
        print("\n[TC-M14-04] 搜索场景")
        try:
            r = setup_router()
            r.set_scene_request_query(lambda: SceneJudgmentRequest(
                request_id="T04", task_description="什么是EM-Core架构"
            ))
            result = r.run_judgment_cycle()
            assert result is not None
            assert result.primary_scene == SceneCategory.SEARCH
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M14-05: 多场景匹配（次选场景）
        print("\n[TC-M14-05] 多场景匹配")
        try:
            r = setup_router()
            r.set_scene_request_query(lambda: SceneJudgmentRequest(
                request_id="T05", task_description="搜索最新AI新闻并生成总结"
            ))
            result = r.run_judgment_cycle()
            assert result is not None
            assert result.primary_scene in (SceneCategory.SEARCH, SceneCategory.CREATION)
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M14-06: 紧急熔断
        print("\n[TC-M14-06] 紧急熔断")
        try:
            r = setup_router()
            r.emergency_shutdown()
            assert r.state == RouterState.SYSTEM_PAUSED
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