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

依赖模块: 无
被依赖模块: ag-ecc-10, ag-ecc-01, ag-mem-11, ag-mem-01

安全约束:
  E-01: 情绪意图推断仅基于会话内的交互元数据与文本语义特征，不得存储或分析用户的原始个人身份信息
  E-02: 情绪推断结果仅供上游模块参考，不得直接触发任何不可逆的系统操作
  E-03: 模型更新必须通过签名校验，防止恶意模型注入
  E-04: 本库完全独立于漏斗记忆系统，不参与记忆沉淀、晋升、遗忘任何环节

版本: V1.0 (总线集成版)
"""

import time
import uuid
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum

from memory_bus import InternalBus, Message


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


class EmotionIntentEngine:
    module_id = "ag-mem-46"
    module_name = "用户情绪意图感知库"
    version = "V1.0"
    _model_version = "1.0.0"

    # 情绪关键词
    POSITIVE_KEYWORDS = ["谢谢", "感谢", "很好", "不错", "满意", "太棒了", "厉害", "喜欢"]
    CONFUSED_KEYWORDS = ["什么意思", "不懂", "不明白", "怎么用", "什么是", "如何", "为什么"]
    ANXIOUS_KEYWORDS = ["快点", "着急", "赶紧", "急", "立刻", "马上", "快"]
    ANGRY_KEYWORDS = ["垃圾", "废物", "什么破", "投诉", "差劲"]
    DISAPPOINTED_KEYWORDS = ["算了", "就这样吧", "没意思", "不行"]

    # 意图关键词
    HELP_KEYWORDS = ["帮助", "帮我", "怎么办", "不会", "教我", "怎么做"]
    DISSAT_KEYWORDS = ["投诉", "转人工", "客服", "退款", "赔偿"]
    EXPLORE_KEYWORDS = ["试试", "看看", "浏览", "有什么", "能做什么"]
    CONFIRM_KEYWORDS = ["对吗", "确认", "是不是", "对吧", "是这个吗"]
    TASK_KEYWORDS = ["执行", "调用", "运行", "查询", "获取", "删除", "创建"]

    def __init__(self):
        self.bus: Optional[InternalBus] = None
        self.state = EngineState.NORMAL_SERVICE
        self._recent_queries: int = 0
        self._pending_logs: List[Dict[str, Any]] = []

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    # ====================== 主循环 ======================
    def emotion_intent_engine_main_loop(self):
        if self.state == EngineState.SYSTEM_PAUSED:
            return

        if self.bus:
            self.bus.process_batch(10)

    # ====================== 总线消息入口 ======================
    def handle_message(self, msg: Message):
        if not isinstance(msg.data, dict):
            return

        if msg.topic == "ag-mem-46.emotion_query":
            self._handle_query(msg)
            return

    def _handle_query(self, msg: Message):
        """处理情绪意图查询请求"""
        data = msg.data
        request_id = data.get("request_id", "")
        session_id = data.get("session_id", "")

        explicit_feedback = data.get("explicit_feedback")
        recent_texts = data.get("recent_text_sequence", [])
        rhythm = data.get("interaction_rhythm", {})

        result = self._infer(explicit_feedback, recent_texts, rhythm)

        if self.bus:
            self.bus.publish(
                topic=f"{msg.source_module}.emotion_result",
                source_module=self.module_id,
                data={
                    "request_id": request_id,
                    "session_id": session_id,
                    "emotion_label": result[0],
                    "emotion_confidence": result[1],
                    "intent_label": result[2],
                    "intent_confidence": result[3],
                    "perception_basis": result[4],
                    "model_version": self._model_version
                },
                target_module=msg.source_module,
                correlation_id=msg.correlation_id
            )

        self._recent_queries += 1

    def _infer(self, explicit_feedback: Optional[str], texts: List[str],
               rhythm: Dict[str, Any]) -> Tuple[str, float, str, float, str]:
        # 快速路径1：显式正向反馈
        if explicit_feedback in ("点赞", "满意", "感谢"):
            intent, _, _ = self._infer_intent(" ".join(texts))
            return (EmotionLabel.SATISFIED.value, 0.95,
                    intent.value, 0.70, "显式正向反馈")

        # 快速路径2：显式负向反馈
        if explicit_feedback in ("点踩", "投诉", "不满"):
            return (EmotionLabel.ANGRY.value, 0.90,
                    IntentLabel.EXPRESS_DISSAT.value, 0.90, "显式负向反馈")

        # 快速路径3：高频重试 → 焦虑
        if rhythm.get("consecutive_retries", 0) >= 3 and rhythm.get("avg_response_interval", 10) < 2:
            return (EmotionLabel.ANXIOUS.value, 0.75,
                    IntentLabel.SEEK_HELP.value, 0.70, "高频重试+快速响应")

        # 标准路径：基于文本关键词
        text = " ".join(texts)
        emotion, emotion_conf, emotion_basis = self._infer_emotion(text)
        intent, intent_conf, intent_basis = self._infer_intent(text)

        return (emotion.value, emotion_conf, intent.value, intent_conf,
                f"情绪: {emotion_basis}; 意图: {intent_basis}")

    def _infer_emotion(self, text: str) -> Tuple[EmotionLabel, float, str]:
        scores = {label: 0.0 for label in EmotionLabel}
        for kw in self.POSITIVE_KEYWORDS:
            if kw in text: scores[EmotionLabel.SATISFIED] += 1.0
        for kw in self.CONFUSED_KEYWORDS:
            if kw in text: scores[EmotionLabel.CONFUSED] += 1.0
        for kw in self.ANXIOUS_KEYWORDS:
            if kw in text: scores[EmotionLabel.ANXIOUS] += 1.0
        for kw in self.ANGRY_KEYWORDS:
            if kw in text: scores[EmotionLabel.ANGRY] += 1.0
        for kw in self.DISAPPOINTED_KEYWORDS:
            if kw in text: scores[EmotionLabel.DISAPPOINTED] += 1.0

        best = max(scores, key=scores.get)
        if scores[best] > 0:
            return best, min(0.5 + scores[best] * 0.15, 0.95), f"关键词匹配({best.value})"
        return EmotionLabel.CALM, 0.40, "无显著情绪关键词"

    def _infer_intent(self, text: str) -> Tuple[IntentLabel, float, str]:
        scores = {label: 0.0 for label in IntentLabel}
        for kw in self.HELP_KEYWORDS:
            if kw in text: scores[IntentLabel.SEEK_HELP] += 1.0
        for kw in self.DISSAT_KEYWORDS:
            if kw in text: scores[IntentLabel.EXPRESS_DISSAT] += 1.0
        for kw in self.EXPLORE_KEYWORDS:
            if kw in text: scores[IntentLabel.EXPLORE] += 1.0
        for kw in self.CONFIRM_KEYWORDS:
            if kw in text: scores[IntentLabel.CONFIRM_INFO] += 1.0
        for kw in self.TASK_KEYWORDS:
            if kw in text: scores[IntentLabel.TASK_EXEC] += 1.0

        best = max(scores, key=scores.get)
        if scores[best] > 0:
            return best, min(0.5 + scores[best] * 0.15, 0.95), f"关键词匹配({best.value})"
        return IntentLabel.CASUAL_CHAT, 0.40, "无显著意图关键词"

    # ====================== 管理接口 ======================
    def emergency_shutdown(self):
        self.state = EngineState.SYSTEM_PAUSED
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