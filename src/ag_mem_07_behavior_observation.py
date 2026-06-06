#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-07
模块名称: 用户行为观测记录单元
所属分区: 二、漏斗一：用户画像漏斗
核心职责: 持续观测并结构化记录用户在交互过程中的操作行为、偏好选择与反馈信号。
          将原始交互事件转化为标准化的行为观测条目，包含行为类型、上下文场景、
          时间戳与关联会话ID。通过 ag-mem-06 隔离管控校验后写入当前活跃画像槽。
          为偏好判定（ag-mem-09）和偏好统计（ag-mem-10）提供原始数据来源。
          不参与任何行为判定或认知决策，仅负责行为数据的采集与结构化存储。

依赖模块:
    ag-mem-02(漏斗一专属调度单元), ag-mem-06(画像槽数据隔离管控单元),
    ag-mem-08(上下文场景标记单元)
被依赖模块:
    ag-mem-09(偏好判定标签单元), ag-mem-10(偏好累积统计单元)

安全约束:
  S-01: 所有行为观测条目在写入画像槽前必须通过 ag-mem-06 隔离管控校验，禁止绕过
  S-02: 行为条目中不得包含用户的原始输入内容，仅保留行为元数据
  S-03: 缓冲区中的数据在未写入画像槽前仅存于内存，进程重启后自动丢弃
  S-04: 写入失败时缓冲区数据保留重试，重试超过3次仍未成功则丢弃并上报告警

版本: V1.0
"""

import time
import uuid
from typing import Any, Dict, List, Optional
from enum import Enum

from memory_bus import InternalBus, Message


class RecordingState(Enum):
    WAITING_SESSION = "waiting_session"
    NORMAL_RECORDING = "normal_recording"
    BATCH_BUFFERING = "batch_buffering"
    WRITE_PAUSED = "write_paused"
    SYSTEM_PAUSED = "system_paused"


class BehaviorType(Enum):
    TEXT_INPUT = "text_input"
    VOICE_INPUT = "voice_input"
    BUTTON_CLICK = "button_click"
    MENU_SELECT = "menu_select"
    TOOL_INVOKE = "tool_invoke"
    RESULT_VIEW = "result_view"
    RESULT_COPY = "result_copy"
    RESULT_SHARE = "result_share"
    FEEDBACK_LIKE = "feedback_like"
    FEEDBACK_DISLIKE = "feedback_dislike"
    FEEDBACK_SKIP = "feedback_skip"
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    ERROR_ENCOUNTER = "error_encounter"
    RETRY_ACTION = "retry_action"


class BehaviorObservationRecorder:
    module_id = "ag-mem-07"
    module_name = "用户行为观测记录单元"
    version = "V1.0"

    BUFFER_THRESHOLD = 20
    BATCH_INTERVAL_SEC = 5.0
    MAX_BUFFER_SIZE = 200
    MAX_RETRY_COUNT = 3

    # 缓冲区溢出时优先保留的行为类型
    PRIORITY_BEHAVIOR_TYPES = {
        BehaviorType.FEEDBACK_LIKE,
        BehaviorType.FEEDBACK_DISLIKE,
        BehaviorType.FEEDBACK_SKIP,
        BehaviorType.ERROR_ENCOUNTER,
    }

    def __init__(self):
        self.bus: Optional[InternalBus] = None

        self.state = RecordingState.WAITING_SESSION
        self._buffer: List[Dict[str, Any]] = []
        self._active_slot_id: Optional[str] = None
        self._active_user_id: Optional[str] = None
        self._current_session_id: Optional[str] = None
        self._last_batch_time = time.time()
        self._total_recorded: int = 0
        self._retry_count: int = 0
        self._pending_logs: List[Dict[str, Any]] = []

        self._write_token: Optional[Dict[str, Any]] = None
        self._pending_token_request: bool = False

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    # ====================== 主循环 ======================
    def behavior_observation_main_loop(self):
        if self.state == RecordingState.SYSTEM_PAUSED:
            return

        if self.bus:
            self.bus.process_batch(10)

        now = time.time()

        # 定时批量写入
        if now - self._last_batch_time >= self.BATCH_INTERVAL_SEC and self._buffer:
            self._perform_batch_write()

        # 写入暂停重试
        if self.state == RecordingState.WRITE_PAUSED:
            self._retry_write()

    # ====================== 总线消息入口 ======================
    def handle_message(self, msg: Message):
        if not isinstance(msg.data, dict):
            return

        # 更新活跃槽位（来自 ag-mem-02）
        if msg.topic == "ag-mem-07.active_slot":
            self._active_slot_id = msg.data.get("slot_id", "")
            self._active_user_id = msg.data.get("user_id", "")
            self._current_session_id = msg.data.get("session_id", "")
            if self.state == RecordingState.WAITING_SESSION:
                self.state = RecordingState.NORMAL_RECORDING
            return

        # 接收交互事件
        if msg.topic == "ag-mem-07.interaction_event":
            self._handle_interaction_event(msg.data)
            return

        # 场景标签（来自 ag-mem-08）
        if msg.topic == "ag-mem-07.scene_label":
            if self._buffer:
                self._buffer[-1]["scene_label"] = {
                    "scene_category": msg.data.get("scene_category", ""),
                    "task_type": msg.data.get("task_type", ""),
                    "interaction_phase": msg.data.get("interaction_phase", ""),
                    "confidence": msg.data.get("confidence", 0.0)
                }
            return

        # 写入令牌签发（来自 ag-mem-06）
        if msg.topic == "ag-mem-07.access_token":
            self._write_token = msg.data
            self._pending_token_request = False
            return

        # 令牌申请被拒
        if msg.topic == "ag-mem-07.access_denied":
            self._write_token = None
            self._pending_token_request = False
            return

    def _handle_interaction_event(self, data: Dict[str, Any]):
        """接收原始交互事件，构建观测条目并加入缓冲区"""
        if self.state == RecordingState.WAITING_SESSION:
            return  # 无活跃槽位，丢弃事件

        event_type_str = data.get("event_type", "")
        # 会话结束事件特殊处理：立即强制写入
        if event_type_str == BehaviorType.SESSION_END.value:
            entry = self._build_entry(data, event_type_str)
            self._buffer.append(entry)
            self._perform_batch_write()
            return

        try:
            behavior_type = BehaviorType(event_type_str)
        except ValueError:
            behavior_type = BehaviorType.TEXT_INPUT

        entry = self._build_entry(data, event_type_str)

        # 关联前置工具调用
        if behavior_type in (BehaviorType.RESULT_VIEW, BehaviorType.FEEDBACK_LIKE,
                              BehaviorType.FEEDBACK_DISLIKE, BehaviorType.RETRY_ACTION):
            for past in reversed(self._buffer):
                if past.get("behavior_type") == BehaviorType.TOOL_INVOKE.value:
                    entry["related_entry_id"] = past["entry_id"]
                    break

        self._buffer.append(entry)

        # 缓冲区溢出保护
        if len(self._buffer) > self.MAX_BUFFER_SIZE:
            discard_index = -1
            for i, item in enumerate(self._buffer):
                try:
                    bt = BehaviorType(item.get("behavior_type", ""))
                except ValueError:
                    bt = BehaviorType.TEXT_INPUT
                if bt not in self.PRIORITY_BEHAVIOR_TYPES:
                    discard_index = i
                    break
            if discard_index >= 0:
                del self._buffer[discard_index]
                self._log_event("BUFFER_OVERFLOW_DISCARDED", {"discarded_entry_id": entry.get("entry_id")})
            else:
                del self._buffer[0]
                self._log_event("BUFFER_OVERFLOW_CRITICAL", {"discarded_entry_id": entry.get("entry_id")})

        # 缓冲区达到阈值时触发批量写入
        if len(self._buffer) >= self.BUFFER_THRESHOLD:
            self._perform_batch_write()

    def _build_entry(self, data: Dict[str, Any], behavior_type_str: str) -> Dict[str, Any]:
        """构建标准化的行为观测条目"""
        return {
            "entry_id": f"OBS-{uuid.uuid4().hex[:8]}",
            "session_id": self._current_session_id or data.get("session_id", ""),
            "user_id": self._active_user_id or "",
            "slot_id": self._active_slot_id or "",
            "behavior_type": behavior_type_str,
            "behavior_params": data.get("event_params", {}),
            "related_entry_id": None,
            "scene_label": None,
            "timestamp": data.get("timestamp", time.time())
        }

    # ====================== 批量写入 ======================
    def _perform_batch_write(self):
        self.state = RecordingState.BATCH_BUFFERING
        self._last_batch_time = time.time()

        batch = self._buffer[:self.BUFFER_THRESHOLD]
        if not batch:
            self.state = RecordingState.NORMAL_RECORDING
            return

        # 如果没有有效令牌，先请求令牌
        if self._write_token is None or self._write_token.get("expires_at", 0) <= time.time():
            self._request_write_token()
            self.state = RecordingState.WRITE_PAUSED
            return

        # 执行写入（模拟成功，集成时替换为真实存储调用）
        success = self._send_batch_to_slot(batch)
        if success:
            self._buffer = self._buffer[len(batch):]
            self._total_recorded += len(batch)
            self._retry_count = 0
            # 发布观测条目给偏好判定与统计模块
            if self.bus:
                for entry in batch:
                    self.bus.publish("ag-mem-09.observation_entry", self.module_id, entry)
                    self.bus.publish("ag-mem-10.observation_entry", self.module_id, entry)
            self.state = RecordingState.NORMAL_RECORDING
        else:
            self._retry_count += 1
            if self._retry_count >= self.MAX_RETRY_COUNT:
                self._buffer = self._buffer[len(batch):]
                self._retry_count = 0
                self._log_event("BATCH_DISCARDED", {"count": len(batch)})
                self.state = RecordingState.NORMAL_RECORDING
            else:
                self.state = RecordingState.WRITE_PAUSED

    def _request_write_token(self):
        """通过总线向 ag-mem-06 申请写入令牌（操作类型必须与 ag-mem-06 枚举一致）"""
        if self._pending_token_request or not self.bus or not self._active_slot_id:
            return
        self._pending_token_request = True
        self.bus.publish_to_module(
            target_module="ag-mem-06",
            event_type="access_request",
            source_module=self.module_id,
            data={
                "source_module": self.module_id,
                "operation": "写",
                "target_slot_id": self._active_slot_id
            }
        )

    def _send_batch_to_slot(self, batch: List[Dict[str, Any]]) -> bool:
        """实际写入画像槽（当前为模拟实现，集成时替换为真实存储调用）"""
        # TODO: 集成时替换为 ag-mem-02 提供的槽位写入接口
        return True

    def _retry_write(self):
        if self._buffer:
            self._perform_batch_write()

    # ====================== 管理接口 ======================
    def emergency_shutdown(self):
        self.state = RecordingState.SYSTEM_PAUSED
        if self._buffer:
            self._perform_batch_write()
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