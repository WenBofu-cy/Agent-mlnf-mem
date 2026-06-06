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
    ag-mem-07(用户行为观测记录单元)
被依赖模块:
    ag-mem-07, ag-mem-09(偏好判定标签单元)

安全约束:
  S-01: 场景判定仅基于行为元数据（类型、顺序），不得访问用户原始输入内容
  S-02: 本模块不持久化任何用户数据，判定过程完全基于内存中的行为序列快照
  S-03: 数据不足时不得强行猜测场景，必须标记低置信度并明确告知下游模块

版本: V1.0 (最终修复版)
"""

import time
import uuid
from typing import Any, Dict, List, Optional
from enum import Enum

from memory_bus import InternalBus, Message
# 导入ag-mem-07行为枚举，避免硬编码错误
from ag_mem_07_behavior_observation import BehaviorType


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
    TASK_START = "task_start"
    EXECUTING = "executing"
    RESULT_EVAL = "result_eval"
    TASK_END = "task_end"


class ContextSceneMarker:
    module_id = "ag-mem-08"
    module_name = "上下文场景标记单元"
    version = "V1.0"

    def __init__(self):
        self.bus: Optional[InternalBus] = None

        self.state = MarkerState.IDLE
        self._last_scene_label: Optional[Dict[str, Any]] = None
        self._scene_switch_count: int = 0
        self._pending_logs: List[Dict[str, Any]] = []

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    # ====================== 主循环 ======================
    def scene_marker_main_loop(self):
        if self.state == MarkerState.SYSTEM_PAUSED:
            return

        if self.bus:
            self.bus.process_batch(10)

    # ====================== 总线消息入口 ======================
    def handle_message(self, msg: Message):
        if not isinstance(msg.data, dict):
            return

        if msg.topic == "ag-mem-08.scene_query":
            self._handle_scene_query(msg)
            return

    def _handle_scene_query(self, msg: Message):
        """处理场景查询，返回场景标签"""
        behaviors = msg.data.get("recent_behaviors", [])
        self.state = MarkerState.ANALYZING

        result = self._determine_scene(behaviors)
        self.state = MarkerState.JUDGED

        # 场景切换通知（连续3次不同才触发）
        if self._last_scene_label:
            if self._last_scene_label.get("scene_category") != result["scene_category"]:
                self._scene_switch_count += 1
                if self._scene_switch_count >= 3:
                    if self.bus:
                        self.bus.publish(
                            topic="ag-mem-09.scene_switch",
                            source_module=self.module_id,
                            data={
                                "previous_scene": self._last_scene_label["scene_category"],
                                "new_scene": result["scene_category"]
                            }
                        )
                    self._scene_switch_count = 0
            else:
                self._scene_switch_count = 0

        self._last_scene_label = result

        # 返回结果给请求方
        if self.bus:
            self.bus.publish(
                topic=f"{msg.source_module}.scene_label",
                source_module=self.module_id,
                data=result,
                target_module=msg.source_module,
                correlation_id=msg.correlation_id
            )

        self.state = MarkerState.IDLE

    def _determine_scene(self, behaviors: List[Dict[str, Any]]) -> Dict[str, Any]:
        """基于行为元数据序列判定场景类别与交互阶段 (严格遵循S-01，不访问原始输入)"""
        if not behaviors:
            self.state = MarkerState.INSUFFICIENT_DATA
            return {
                "scene_category": SceneCategory.GENERAL.value,
                "task_type": "",
                "interaction_phase": InteractionPhase.TASK_START.value,
                "confidence": 0.3,
                "judgment_basis": "无可用的行为数据"
            }

        # 提取关键行为序列（排除会话起止事件）
        key_behaviors = [
            b for b in behaviors
            if b.get("behavior_type") not in (BehaviorType.SESSION_START.value, BehaviorType.SESSION_END.value)
        ]
        if not key_behaviors:
            return {
                "scene_category": SceneCategory.GENERAL.value,
                "task_type": "",
                "interaction_phase": InteractionPhase.TASK_START.value,
                "confidence": 0.3,
                "judgment_basis": "无有效行为"
            }

        # 第一步：基于最后一条关键行为判定
        last_b = key_behaviors[-1]
        btype = last_b.get("behavior_type", "")
        params = last_b.get("behavior_params", {})
        tool_name = params.get("tool_name", "")

        # 工具调用场景
        if btype == BehaviorType.TOOL_INVOKE.value:
            return {
                "scene_category": SceneCategory.TOOL_CALL.value,
                "task_type": tool_name or "unknown_tool",
                "interaction_phase": InteractionPhase.EXECUTING.value,
                "confidence": 0.90,
                "judgment_basis": "检测到工具调用行为"
            }

        # 结果复制通常表示搜索/检索完成
        if btype == BehaviorType.RESULT_COPY.value:
            # 优先判断是否为搜索工具调用后的复制
            for b in key_behaviors[-3:]:
                if (b.get("behavior_type") == BehaviorType.TOOL_INVOKE.value and
                    b.get("behavior_params", {}).get("tool_name") in ["search", "web_search", "knowledge_base"]):
                    return {
                        "scene_category": SceneCategory.SEARCH.value,
                        "task_type": "search",
                        "interaction_phase": InteractionPhase.RESULT_EVAL.value,
                        "confidence": 0.80,
                        "judgment_basis": "搜索工具调用后复制结果"
                    }
            # 非搜索类复制
            return {
                "scene_category": SceneCategory.GENERAL.value,
                "task_type": "result_copy",
                "interaction_phase": InteractionPhase.RESULT_EVAL.value,
                "confidence": 0.75,
                "judgment_basis": "用户复制结果"
            }

        # 创作生成场景：检测到具有生成意图的行为参数
        if params.get("intent") == "generation" or params.get("task_type") == "creation":
            return {
                "scene_category": SceneCategory.CREATION.value,
                "task_type": "creation",
                "interaction_phase": InteractionPhase.TASK_START.value,
                "confidence": 0.80,
                "judgment_basis": "检测到创作生成意图"
            }

        # 第二步：回溯最近3条行为的类型序列进行模式匹配
        recent_types = [b.get("behavior_type") for b in key_behaviors[-3:]]
        # 连续问答模式 (TEXT_INPUT → RESULT_VIEW → TEXT_INPUT)
        if len(recent_types) >= 2 and recent_types[-2:] == [BehaviorType.TEXT_INPUT.value, BehaviorType.RESULT_VIEW.value]:
            # 再往前看是否还有一轮
            if len(recent_types) == 3 and recent_types[-3] == BehaviorType.TEXT_INPUT.value:
                return {
                    "scene_category": SceneCategory.DIALOGUE.value,
                    "task_type": "chat",
                    "interaction_phase": InteractionPhase.EXECUTING.value,
                    "confidence": 0.70,
                    "judgment_basis": "多轮问答模式匹配"
                }
            # 单轮问答暂时也归为对话
            return {
                "scene_category": SceneCategory.DIALOGUE.value,
                "task_type": "chat",
                "interaction_phase": InteractionPhase.RESULT_EVAL.value,
                "confidence": 0.55,
                "judgment_basis": "单轮问答模式"
            }

        # 默认：通用任务
        return {
            "scene_category": SceneCategory.GENERAL.value,
            "task_type": "",
            "interaction_phase": InteractionPhase.TASK_START.value,
            "confidence": 0.50,
            "judgment_basis": "无匹配特征，使用默认场景"
        }

    # ====================== 管理接口 ======================
    def emergency_shutdown(self):
        self.state = MarkerState.SYSTEM_PAUSED
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