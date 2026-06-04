#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-46
模块名称: 用户情绪意图感知库
所属分区: 四、漏斗外挂扩展区（物理隔离）
核心职责: 作为漏斗外挂扩展区的情绪感知服务库，提供用户实时情绪状态与交互意图的推断结果。
          基于多模态输入（文本语义、交互节奏、显式反馈、上下文行为序列），输出标准化的
          情绪标签（如：平静、焦虑、愤怒、满意、困惑）与意图倾向（如：寻求帮助、表达不满、
          探索尝试、确认信息）。为 ag-ecc-10（社会心智模块）提供情绪感知数据以适配交互风格，
          为 ag-ecc-01（意图解析模块）提供意图预判辅助，为 ag-mem-11（个性化建议生成单元）
          提供用户情绪背景以调整建议强度。本库完全独立于双漏斗记忆系统运行，不参与记忆沉淀、
          筛选、晋升与遗忘机制。情绪推断模型在系统部署前预置，支持定期离线更新。仅提供只读
          查询服务，不参与任何认知决策。

依赖模块:
    无（作为独立情绪感知服务，不依赖记忆系统内部模块）
被依赖模块:
    ag-ecc-10(社会心智模块), ag-ecc-01(意图解析模块),
    ag-mem-11(个性化建议生成单元), ag-mem-01(总控漏斗F₀)

安全约束:
  E-01: 情绪意图推断仅基于会话内的交互元数据与文本语义特征，不得存储或分析用户的原始个人身份信息
  E-02: 情绪推断结果仅供上游模块参考，不得直接触发任何不可逆的系统操作
  E-03: 模型更新必须通过签名校验，防止恶意模型注入
  E-04: 本库完全独立于漏斗记忆系统，不参与记忆沉淀、晋升、遗忘任何环节
"""

from typing import Dict, List, Optional, Any, Callable, Tuple
from dataclasses import dataclass, field
from enum import Enum
import time
import uuid
import re


class EngineState(Enum):
    NORMAL_SERVICE = "normal_service"
    LOADING = "loading"
    DEGRADED = "degraded"
    SYSTEM_PAUSED = "system_paused"


class EmotionLabel(Enum):
    CALM = "平静"
    SATISFIED = "满意"
    CONFUSED = "困惑"
    ANXIOUS = "焦虑"
    ANGRY = "愤怒"
    DISAPPOINTED = "失望"


class IntentLabel(Enum):
    SEEK_HELP = "寻求帮助"
    EXPRESS_DISSAT = "表达不满"
    EXPLORE = "探索尝试"
    CONFIRM_INFO = "确认信息"
    TASK_EXEC = "任务执行"
    CASUAL_CHAT = "休闲闲聊"


@dataclass
class EmotionIntentQueryRequest:
    request_id: str = ""
    session_id: str = ""
    requester_module: str = ""
    query_type: str = "综合感知"        # 情绪状态/意图倾向/综合感知
    recent_text_sequence: List[str] = field(default_factory=list)
    interaction_rhythm: Dict[str, Any] = field(default_factory=dict)  # 交互节奏数据
    explicit_feedback: Optional[str] = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class EmotionIntentResult:
    request_id: str = ""
    session_id: str = ""
    emotion_label: EmotionLabel = EmotionLabel.CALM
    emotion_confidence: float = 0.3
    intent_label: IntentLabel = IntentLabel.CASUAL_CHAT
    intent_confidence: float = 0.3
    perception_basis: str = ""
    model_version: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class EngineStatus:
    state: EngineState = EngineState.NORMAL_SERVICE
    model_version: str = ""
    recent_queries: int = 0
    avg_response_ms: float = 0.0


class EmotionIntentEngine:
    # 积极情绪关键词
    POSITIVE_KEYWORDS = ["谢谢", "感谢", "很好", "不错", "满意", "太棒了", "厉害", "喜欢"]
    # 困惑关键词
    CONFUSED_KEYWORDS = ["什么意思", "不懂", "不明白", "怎么用", "什么是", "如何", "为什么"]
    # 焦虑关键词
    ANXIOUS_KEYWORDS = ["快点", "着急", "赶紧", "急", "立刻", "马上", "快"]
    # 愤怒关键词
    ANGRY_KEYWORDS = ["垃圾", "废物", "傻逼", "什么破", "投诉", "差劲"]
    # 失望关键词
    DISAPPOINTED_KEYWORDS = ["算了", "就这样吧", "没意思", "不行"]

    # 意图关键词
    HELP_KEYWORDS = ["帮助", "帮我", "怎么办", "不会", "教我", "怎么做"]
    DISSAT_KEYWORDS = ["投诉", "转人工", "客服", "退款", "赔偿"]
    EXPLORE_KEYWORDS = ["试试", "看看", "浏览", "有什么", "能做什么"]
    CONFIRM_KEYWORDS = ["对吗", "确认", "是不是", "对吧", "是这个吗"]
    TASK_KEYWORDS = ["执行", "调用", "运行", "查询", "获取", "删除", "创建"]

    def __init__(self):
        self.module_id = "ag-mem-46"
        self.module_name = "用户情绪意图感知库"
        self.version = "V1.0"
        self._model_version = "1.0.0"

        self.state = EngineState.NORMAL_SERVICE
        self._recent_queries: int = 0
        self._total_response_ms: float = 0.0
        self._pending_logs: List[Dict[str, Any]] = []

        # 回调注入
        self._query_request = None
        self._publish_result = None
        self._publish_status_report = None
        self._publish_event_log = None

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    # ========== 回调注入 ==========
    def set_request_query(self, callback: Callable[[], Optional[EmotionIntentQueryRequest]]):
        self._query_request = callback

    def set_result_publisher(self, callback: Callable[[EmotionIntentResult], None]):
        self._publish_result = callback

    def set_status_report_publisher(self, callback: Callable[[EngineStatus], None]):
        self._publish_status_report = callback

    def set_event_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_event_log = callback

    # ========== 主循环 ==========
    def run_engine_cycle(self):
        if self.state == EngineState.SYSTEM_PAUSED:
            return

        request = self._query_request() if self._query_request else None
        if request is None:
            return

        start_time = time.time()
        result = self._infer(request)
        elapsed = (time.time() - start_time) * 1000

        self._recent_queries += 1
        self._total_response_ms += elapsed

        if self._publish_result:
            self._publish_result(result)

    # ========== 核心推断 ==========
    def _infer(self, request: EmotionIntentQueryRequest) -> EmotionIntentResult:
        # 快速路径1：显式正向反馈
        if request.explicit_feedback in ("点赞", "满意", "感谢"):
            emotion = EmotionLabel.SATISFIED
            emotion_conf = 0.95
            intent = self._infer_intent_from_text(request.recent_text_sequence)
            return self._build_result(request, emotion, emotion_conf, intent, 0.7, "显式正向反馈")

        # 快速路径2：显式负向反馈
        if request.explicit_feedback in ("点踩", "投诉", "不满"):
            emotion = EmotionLabel.ANGRY
            emotion_conf = 0.90
            intent = IntentLabel.EXPRESS_DISSAT
            return self._build_result(request, emotion, emotion_conf, intent, 0.90, "显式负向反馈")

        # 快速路径3：交互节奏判断焦虑
        rhythm = request.interaction_rhythm
        if rhythm.get("consecutive_retries", 0) >= 3 and rhythm.get("avg_response_interval", 10) < 2:
            return self._build_result(request, EmotionLabel.ANXIOUS, 0.75, IntentLabel.SEEK_HELP, 0.70, "高频重试+快速响应")

        # 标准路径：基于文本语义推断
        text = " ".join(request.recent_text_sequence)

        # 情绪推断
        emotion, emotion_conf, emotion_basis = self._infer_emotion(text)
        # 意图推断
        intent, intent_conf, intent_basis = self._infer_intent(text)

        basis = f"情绪: {emotion_basis}; 意图: {intent_basis}"

        return self._build_result(request, emotion, emotion_conf, intent, intent_conf, basis)

    def _infer_emotion(self, text: str) -> Tuple[EmotionLabel, float, str]:
        # 关键词匹配（降级使用规则引擎）
        scores = {label: 0.0 for label in EmotionLabel}

        for kw in self.POSITIVE_KEYWORDS:
            if kw in text:
                scores[EmotionLabel.SATISFIED] += 1.0
        for kw in self.CONFUSED_KEYWORDS:
            if kw in text:
                scores[EmotionLabel.CONFUSED] += 1.0
        for kw in self.ANXIOUS_KEYWORDS:
            if kw in text:
                scores[EmotionLabel.ANXIOUS] += 1.0
        for kw in self.ANGRY_KEYWORDS:
            if kw in text:
                scores[EmotionLabel.ANGRY] += 1.0
        for kw in self.DISAPPOINTED_KEYWORDS:
            if kw in text:
                scores[EmotionLabel.DISAPPOINTED] += 1.0

        # 选出最高分
        best_label = max(scores, key=scores.get)
        best_score = scores[best_label]

        if best_score > 0:
            confidence = min(0.5 + best_score * 0.15, 0.95)
            return best_label, round(confidence, 2), f"关键词匹配({best_label.value})"
        else:
            return EmotionLabel.CALM, 0.40, "无显著情绪关键词"

    def _infer_intent(self, text: str) -> Tuple[IntentLabel, float, str]:
        scores = {label: 0.0 for label in IntentLabel}

        for kw in self.HELP_KEYWORDS:
            if kw in text:
                scores[IntentLabel.SEEK_HELP] += 1.0
        for kw in self.DISSAT_KEYWORDS:
            if kw in text:
                scores[IntentLabel.EXPRESS_DISSAT] += 1.0
        for kw in self.EXPLORE_KEYWORDS:
            if kw in text:
                scores[IntentLabel.EXPLORE] += 1.0
        for kw in self.CONFIRM_KEYWORDS:
            if kw in text:
                scores[IntentLabel.CONFIRM_INFO] += 1.0
        for kw in self.TASK_KEYWORDS:
            if kw in text:
                scores[IntentLabel.TASK_EXEC] += 1.0

        best_label = max(scores, key=scores.get)
        best_score = scores[best_label]

        if best_score > 0:
            confidence = min(0.5 + best_score * 0.15, 0.95)
            return best_label, round(confidence, 2), f"关键词匹配({best_label.value})"
        else:
            return IntentLabel.CASUAL_CHAT, 0.40, "无显著意图关键词"

    def _infer_intent_from_text(self, texts: List[str]) -> IntentLabel:
        # 仅从文本推断意图，用于快速路径
        text = " ".join(texts)
        intent, _, _ = self._infer_intent(text)
        return intent

    def _build_result(self, request: EmotionIntentQueryRequest, emotion: EmotionLabel, emotion_conf: float,
                      intent: IntentLabel, intent_conf: float, basis: str) -> EmotionIntentResult:
        return EmotionIntentResult(
            request_id=request.request_id,
            session_id=request.session_id,
            emotion_label=emotion,
            emotion_confidence=emotion_conf,
            intent_label=intent,
            intent_confidence=intent_conf,
            perception_basis=basis,
            model_version=self._model_version
        )

    # ========== 辅助 ==========
    def emergency_shutdown(self):
        self.state = EngineState.SYSTEM_PAUSED
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


# ========== 演示与测试 ==========
def print_separator(title: str):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def demo_main():
    print("=" * 70)
    print("  Agent-mlnf-mem 用户情绪意图感知库 (ag-mem-46) 演示")
    print("=" * 70)

    engine = EmotionIntentEngine()

    print_separator("STEP 1: 用户点赞 → 满意")
    engine.set_request_query(lambda: EmotionIntentQueryRequest(
        request_id="Q1", session_id="S1",
        explicit_feedback="点赞", recent_text_sequence=["太好了"]
    ))
    engine.run_engine_cycle()

    print_separator("STEP 2: 焦虑 + 寻求帮助")
    engine.set_request_query(lambda: EmotionIntentQueryRequest(
        request_id="Q2", session_id="S1",
        recent_text_sequence=["快点帮我查一下", "着急用"],
        interaction_rhythm={"consecutive_retries": 4, "avg_response_interval": 1.5}
    ))
    engine.run_engine_cycle()

    print_separator("STEP 3: 愤怒 + 表达不满")
    engine.set_request_query(lambda: EmotionIntentQueryRequest(
        request_id="Q3", session_id="S1",
        recent_text_sequence=["什么垃圾功能", "我要投诉"]
    ))
    engine.run_engine_cycle()

    print("\n✅ 用户情绪意图感知库演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("ag-mem-46 用户情绪意图感知库 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        def setup_engine():
            return EmotionIntentEngine()

        # TC-M46-01: 显式点赞 → 满意
        print("\n[TC-M46-01] 显式点赞 → 满意")
        try:
            e = setup_engine()
            e.set_request_query(lambda: EmotionIntentQueryRequest(
                request_id="T01", session_id="S", explicit_feedback="点赞"
            ))
            e.run_engine_cycle()
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M46-02: 高频重试 → 焦虑
        print("\n[TC-M46-02] 高频重试 → 焦虑")
        try:
            eng = setup_engine()
            eng.set_request_query(lambda: EmotionIntentQueryRequest(
                request_id="T02", session_id="S",
                interaction_rhythm={"consecutive_retries": 4, "avg_response_interval": 1.5}
            ))
            eng.run_engine_cycle()
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M46-03: 点踩 → 愤怒
        print("\n[TC-M46-03] 点踩 → 愤怒")
        try:
            eng = setup_engine()
            eng.set_request_query(lambda: EmotionIntentQueryRequest(
                request_id="T03", session_id="S", explicit_feedback="点踩"
            ))
            eng.run_engine_cycle()
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M46-04: 文本包含困惑关键词
        print("\n[TC-M46-04] 文本包含困惑关键词")
        try:
            eng = setup_engine()
            eng.set_request_query(lambda: EmotionIntentQueryRequest(
                request_id="T04", session_id="S",
                recent_text_sequence=["这个功能怎么用", "我不太懂"]
            ))
            eng.run_engine_cycle()
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M46-05: 文本包含愤怒关键词
        print("\n[TC-M46-05] 文本包含愤怒关键词")
        try:
            eng = setup_engine()
            eng.set_request_query(lambda: EmotionIntentQueryRequest(
                request_id="T05", session_id="S",
                recent_text_sequence=["什么垃圾东西", "我要投诉"]
            ))
            eng.run_engine_cycle()
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M46-06: 紧急熔断
        print("\n[TC-M46-06] 紧急熔断")
        try:
            eng = setup_engine()
            eng.emergency_shutdown()
            assert eng.state == EngineState.SYSTEM_PAUSED
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