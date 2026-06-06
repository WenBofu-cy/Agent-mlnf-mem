#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-09
模块名称: 偏好判定标签单元
所属分区: 二、漏斗一：用户画像漏斗
核心职责: 接收 ag-mem-07 输出的标准化行为观测条目，结合 ag-mem-08 的上下文场景标签，
          基于行为类型、反馈强度与频次，判定用户的显式偏好、隐式倾向与偶发行为。
          生成标准化的偏好标签，包含标签维度、置信度与来源行为关联。为偏好统计
          （ag-mem-10）和个性化建议（ag-mem-11）提供基础数据。不参与任何认知决策，
          仅执行基于规则的偏好标签生成。

依赖模块:
    ag-mem-07(用户行为观测记录单元), ag-mem-08(上下文场景标记单元),
    ag-mem-10(偏好累积统计单元)  -- 接收历史基线
被依赖模块:
    ag-mem-10(偏好累积统计单元), ag-mem-11(个性化建议生成单元)

安全约束:
  S-01: 偏好判定仅基于行为元数据与场景标签，不得访问用户原始输入内容
  S-02: 生成的偏好标签不得包含任何用户个人身份信息或敏感内容
  S-03: 置信度低于 0.3 的判定结果不得输出，必须标记为"无法判定"
  S-04: 本模块不持久化原始行为数据，仅保留生成的偏好标签结果
  S-05: 负面偏好标签的置信度必须高于 0.7 才能输出，避免误判

版本: V1.0 (CPEC 标签对齐 · 原始行为字段透传)
"""

import time
import uuid
from typing import Any, Dict, List, Optional
from enum import Enum

from memory_bus import InternalBus, Message
from ag_mem_07_behavior_observation import BehaviorType


class LabelingState(Enum):
    IDLE = "idle"
    PROCESSING = "processing"
    LABEL_GENERATED = "label_generated"
    INSUFFICIENT_DATA = "insufficient_data"
    SYSTEM_PAUSED = "system_paused"


class PreferenceType(Enum):
    EXPLICIT_PREFERENCE = "显式偏好"
    IMPLICIT_TENDENCY = "隐式倾向"
    OCCASIONAL_BEHAVIOR = "偶发行为"
    NEGATIVE_PREFERENCE = "负面偏好"


class PreferenceLabel:
    """偏好标签数据结构（对齐 CPEC 规格）"""
    def __init__(self, label_id: str = "", user_id: str = "", slot_id: str = "",
                 scene_category: str = "", label_dimension: str = "", label_value: str = "",
                 preference_type: str = "", confidence: float = 0.0, source_behavior_id: str = "",
                 behavior_type: str = "", behavior_params: Optional[Dict[str, Any]] = None,
                 timestamp: float = None):
        self.label_id = label_id
        self.user_id = user_id
        self.slot_id = slot_id
        self.scene_category = scene_category
        self.label_dimension = label_dimension
        self.label_value = label_value
        self.preference_type = preference_type
        self.confidence = confidence
        self.source_behavior_id = source_behavior_id
        self.behavior_type = behavior_type
        self.behavior_params = behavior_params or {}
        self.timestamp = timestamp or time.time()

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()


class PreferenceLabelJudge:
    module_id = "ag-mem-09"
    module_name = "偏好判定标签单元"
    version = "V1.0"

    NEGATIVE_CONFIDENCE_THRESHOLD = 0.7
    GENERAL_CONFIDENCE_THRESHOLD = 0.3
    HIGH_FREQ_THRESHOLD = 5
    VIEW_DURATION_RATIO = 1.5
    RETRY_THRESHOLD = 2

    def __init__(self):
        self.bus: Optional[InternalBus] = None

        self.state = LabelingState.IDLE
        self._current_scene: str = "general"
        self._label_cache: List[PreferenceLabel] = []
        self._baseline: Optional[Dict[str, Any]] = None
        self._behavior_counts: Dict[str, Any] = {}
        self._pending_logs: List[Dict[str, Any]] = []

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    # ====================== 主循环 ======================
    def preference_labeling_main_loop(self):
        if self.state == LabelingState.SYSTEM_PAUSED:
            return

        if self.bus:
            self.bus.process_batch(10)

    # ====================== 总线消息入口 ======================
    def handle_message(self, msg: Message):
        if not isinstance(msg.data, dict):
            return

        if msg.topic == "ag-mem-09.observation_entry":
            self._handle_observation_entry(msg)
            return

        if msg.topic == "ag-mem-09.scene_switch":
            self._handle_scene_switch(msg)
            return

        if msg.topic == "ag-mem-09.preference_baseline":
            self._baseline = msg.data
            self._log_event("BASELINE_RECEIVED", {"has_data": bool(msg.data)})
            return

    def _handle_observation_entry(self, msg: Message):
        """处理行为观测条目，生成偏好标签"""
        behavior = msg.data
        self.state = LabelingState.PROCESSING

        try:
            if not behavior.get("entry_id") or not behavior.get("user_id"):
                self._log_event("INVALID_BEHAVIOR", {"reason": "缺少必要字段"})
                return

            label = self._generate_preference_label(behavior)
            if label and label.confidence >= self.GENERAL_CONFIDENCE_THRESHOLD:
                if (label.preference_type == PreferenceType.NEGATIVE_PREFERENCE.value and
                    label.confidence < self.NEGATIVE_CONFIDENCE_THRESHOLD):
                    self._log_event("NEGATIVE_LABEL_DISCARDED", {
                        "confidence": label.confidence,
                        "threshold": self.NEGATIVE_CONFIDENCE_THRESHOLD
                    })
                    return

                self._label_cache.append(label)
                if self.bus:
                    self.bus.publish(
                        topic="ag-mem-10.preference_label",
                        source_module=self.module_id,
                        data=label.to_dict(),
                        target_module="ag-mem-10",
                        correlation_id=msg.correlation_id
                    )
                self._log_event("LABEL_GENERATED", {
                    "label_id": label.label_id,
                    "dimension": label.label_dimension,
                    "confidence": label.confidence
                })
            else:
                self._log_event("NO_LABEL_GENERATED", {
                    "behavior_id": behavior.get("entry_id"),
                    "reason": "置信度不足或无法判定"
                })
        finally:
            self.state = LabelingState.IDLE

    def _handle_scene_switch(self, msg: Message):
        self._current_scene = msg.data.get("new_scene", "general")
        self._log_event("SCENE_SWITCHED", {
            "previous": msg.data.get("previous_scene"),
            "new": self._current_scene
        })

    # ====================== 核心判定逻辑 ======================
    def _generate_preference_label(self, behavior: Dict[str, Any]) -> Optional[PreferenceLabel]:
        """
        依据接口规格文档的判定规则优先级矩阵生成偏好标签。
        接入历史偏好基线后，对查看时长、工具调用频次、跳过次数等进行动态置信度调整。
        无基线时降级为保守判定（低置信度或偶发）。
        """
        btype = behavior.get("behavior_type", "")
        params = behavior.get("behavior_params", {})
        scene = behavior.get("scene_label", {}).get("scene_category", self._current_scene)
        user_id = behavior.get("user_id", "")
        slot_id = behavior.get("slot_id", "")
        entry_id = behavior.get("entry_id", "")

        def make_label(dimension: str, value: str, pref_type: PreferenceType, confidence: float) -> PreferenceLabel:
            return PreferenceLabel(
                label_id=f"LABEL-{uuid.uuid4().hex[:8]}",
                user_id=user_id, slot_id=slot_id,
                scene_category=scene,
                label_dimension=dimension,
                label_value=value,
                preference_type=pref_type.value,
                confidence=confidence,
                source_behavior_id=entry_id,
                behavior_type=btype,          # 透传原始行为类型
                behavior_params=params.copy() # 透传原始行为参数
            )

        # ---------- 显式反馈 ----------
        if btype == BehaviorType.FEEDBACK_LIKE.value:
            return make_label("explicit_positive", "like", PreferenceType.EXPLICIT_PREFERENCE, 0.95)

        if btype == BehaviorType.FEEDBACK_DISLIKE.value:
            return make_label("explicit_negative", "dislike", PreferenceType.NEGATIVE_PREFERENCE, 0.95)

        # ---------- 分享/复制 ----------
        if btype == BehaviorType.RESULT_SHARE.value:
            return make_label("share_behavior", "share", PreferenceType.IMPLICIT_TENDENCY, 0.80)

        if btype == BehaviorType.RESULT_COPY.value:
            return make_label("copy_behavior", "copy", PreferenceType.IMPLICIT_TENDENCY, 0.70)

        # ---------- 查看行为 ----------
        if btype == BehaviorType.RESULT_VIEW.value:
            view_duration = params.get("view_duration", 0)
            baseline_avg = 0
            if self._baseline:
                baseline_avg = self._baseline.get("avg_view_duration", {}).get(scene, 0)
            if baseline_avg > 0 and view_duration > baseline_avg * self.VIEW_DURATION_RATIO:
                return make_label("view_behavior", "view", PreferenceType.IMPLICIT_TENDENCY, 0.65)
            else:
                return make_label("view_behavior", "view", PreferenceType.OCCASIONAL_BEHAVIOR, 0.40)

        # ---------- 重试行为 ----------
        if btype == BehaviorType.RETRY_ACTION.value:
            retry_count = params.get("retry_count", 0)
            if retry_count >= self.RETRY_THRESHOLD:
                return make_label("retry_persistence", "retry", PreferenceType.IMPLICIT_TENDENCY, 0.70)
            else:
                return make_label("retry_attempt", "retry", PreferenceType.OCCASIONAL_BEHAVIOR, 0.40)

        # ---------- 工具调用 ----------
        if btype == BehaviorType.TOOL_INVOKE.value:
            tool_name = params.get("tool_name", "unknown")
            freq = 0
            if self._baseline:
                freq = self._baseline.get("tool_frequencies", {}).get(tool_name, 0)
            if freq >= self.HIGH_FREQ_THRESHOLD:
                return make_label("tool_usage", tool_name, PreferenceType.IMPLICIT_TENDENCY, 0.75)
            else:
                return make_label("tool_usage", tool_name, PreferenceType.OCCASIONAL_BEHAVIOR, 0.40)

        # ---------- 连续跳过 ----------
        if btype == BehaviorType.FEEDBACK_SKIP.value:
            total_skips = 0
            if self._baseline and "skip_count" in self._baseline:
                total_skips = self._baseline["skip_count"]
            else:
                self._update_skip_counter(user_id)
                total_skips = self._behavior_counts.get(f"{user_id}_skip", 0)
            if total_skips >= 3:
                return make_label("skip_pattern", "skip", PreferenceType.NEGATIVE_PREFERENCE, 0.70)
            else:
                return make_label("skip_instance", "skip", PreferenceType.OCCASIONAL_BEHAVIOR, 0.30)

        # ---------- 普通输入行为 ----------
        if btype in (BehaviorType.TEXT_INPUT.value, BehaviorType.VOICE_INPUT.value,
                     BehaviorType.BUTTON_CLICK.value, BehaviorType.MENU_SELECT.value,
                     BehaviorType.SESSION_START.value, BehaviorType.SESSION_END.value,
                     BehaviorType.ERROR_ENCOUNTER.value):
            return make_label("input_activity", btype, PreferenceType.OCCASIONAL_BEHAVIOR, 0.25)

        return None

    def _update_skip_counter(self, user_id: str):
        key = f"{user_id}_skip"
        self._behavior_counts[key] = self._behavior_counts.get(key, 0) + 1

    # ====================== 管理接口 ======================
    def emergency_shutdown(self):
        self.state = LabelingState.SYSTEM_PAUSED
        self._label_cache.clear()
        self._behavior_counts.clear()
        self._baseline = None
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

    def collect_pending_logs(self) -> List[Dict[str, Any]]:
        tmp = self._pending_logs.copy()
        self._pending_logs.clear()
        return tmp