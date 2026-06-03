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
"""

from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
import time
import uuid


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


@dataclass
class UserInteractionEvent:
    session_id: str = ""
    event_type: BehaviorType = BehaviorType.TEXT_INPUT
    event_params: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


@dataclass
class ActiveSlotInfo:
    session_id: str = ""
    slot_id: str = ""
    user_id: str = ""


@dataclass
class SceneLabel:
    scene_category: str = ""
    task_type: str = ""
    interaction_phase: str = ""


@dataclass
class WriteToken:
    token_id: str = ""
    authorized_slot_id: str = ""
    expires_at: float = 0.0


@dataclass
class BehaviorObservationEntry:
    entry_id: str = ""
    session_id: str = ""
    user_id: str = ""
    slot_id: str = ""
    behavior_type: BehaviorType = BehaviorType.TEXT_INPUT
    behavior_params: Dict[str, Any] = field(default_factory=dict)
    scene_label: Optional[SceneLabel] = None
    related_entry_id: Optional[str] = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class BatchWriteResult:
    success: bool = True
    written_count: int = 0
    error_reason: str = ""


@dataclass
class RecordingStatus:
    state: RecordingState = RecordingState.WAITING_SESSION
    total_recorded: int = 0
    buffer_usage_pct: float = 0.0
    write_success_rate: float = 1.0


class BehaviorObservationRecorder:
    BUFFER_THRESHOLD = 20
    BATCH_INTERVAL_SEC = 5.0
    MAX_BUFFER_SIZE = 200
    MAX_RETRY_COUNT = 3

    def __init__(self):
        self.module_id = "ag-mem-07"
        self.module_name = "用户行为观测记录单元"
        self.version = "V1.0"

        self.state = RecordingState.WAITING_SESSION
        self._buffer: List[BehaviorObservationEntry] = []
        self._active_slot_id: Optional[str] = None
        self._active_user_id: Optional[str] = None
        self._current_session_id: Optional[str] = None
        self._last_batch_time = time.time()
        self._total_recorded: int = 0
        self._write_success_total: int = 0
        self._write_attempt_total: int = 0
        self._retry_count: int = 0
        self._pending_logs: List[Dict[str, Any]] = []

        # 回调注入
        self._query_interaction_event = None
        self._query_active_slot_binding = None
        self._query_scene_label = None
        self._query_write_token = None

        self._publish_observation_entry = None
        self._publish_batch_write_request = None
        self._publish_status_report = None
        self._publish_event_log = None

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    def set_interaction_event_query(self, callback: Callable[[], Optional[UserInteractionEvent]]):
        self._query_interaction_event = callback

    def set_active_slot_query(self, callback: Callable[[], Optional[ActiveSlotInfo]]):
        self._query_active_slot_binding = callback

    def set_scene_label_query(self, callback: Callable[[], Optional[SceneLabel]]):
        self._query_scene_label = callback

    def set_write_token_query(self, callback: Callable[[], Optional[WriteToken]]):
        self._query_write_token = callback

    def set_observation_publisher(self, callback: Callable[[List[BehaviorObservationEntry]], None]):
        self._publish_observation_entry = callback

    def set_batch_write_publisher(self, callback: Callable[[str, List[BehaviorObservationEntry], str], None]):
        self._publish_batch_write_request = callback

    def set_status_report_publisher(self, callback: Callable[[RecordingStatus], None]):
        self._publish_status_report = callback

    def set_event_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_event_log = callback

    def run_observation_cycle(self):
        now = time.time()

        if self.state == RecordingState.SYSTEM_PAUSED:
            return

        # 更新活跃槽位绑定
        slot_binding = self._query_active_slot_binding() if self._query_active_slot_binding else None
        if slot_binding:
            self._active_slot_id = slot_binding.slot_id
            self._active_user_id = slot_binding.user_id
            self._current_session_id = slot_binding.session_id
            if self.state == RecordingState.WAITING_SESSION:
                self.state = RecordingState.NORMAL_RECORDING

        if self.state == RecordingState.WAITING_SESSION:
            return

        # 接收交互事件
        event = self._query_interaction_event() if self._query_interaction_event else None
        if event is not None:
            entry = self._build_observation_entry(event)
            self._buffer.append(entry)

            # 检查是否需要批量写入
            if len(self._buffer) >= self.BUFFER_THRESHOLD:
                self._perform_batch_write()

        # 定时批量写入
        if now - self._last_batch_time >= self.BATCH_INTERVAL_SEC and self._buffer:
            self._perform_batch_write()

        # 写入暂停重试
        if self.state == RecordingState.WRITE_PAUSED:
            self._retry_write()

    def _build_observation_entry(self, event: UserInteractionEvent) -> BehaviorObservationEntry:
        entry = BehaviorObservationEntry(
            entry_id=f"OBS-{uuid.uuid4().hex[:8]}",
            session_id=event.session_id or self._current_session_id or "",
            user_id=self._active_user_id or "",
            slot_id=self._active_slot_id or "",
            behavior_type=event.event_type,
            behavior_params=event.event_params,
            timestamp=event.timestamp
        )

        # 关联前置行为
        if event.event_type in (BehaviorType.RESULT_VIEW, BehaviorType.FEEDBACK_LIKE,
                                 BehaviorType.FEEDBACK_DISLIKE, BehaviorType.RETRY_ACTION):
            for past in reversed(self._buffer):
                if past.behavior_type == BehaviorType.TOOL_INVOKE:
                    entry.related_entry_id = past.entry_id
                    break

        # 获取场景标签
        if self._query_scene_label:
            label = self._query_scene_label()
            if label:
                entry.scene_label = label

        return entry

    def _perform_batch_write(self):
        self.state = RecordingState.BATCH_BUFFERING
        self._last_batch_time = time.time()

        batch = self._buffer[:self.BUFFER_THRESHOLD]
        write_result = self._execute_batch_write(batch)

        if write_result.success:
            self._buffer = self._buffer[len(batch):]
            self._total_recorded += write_result.written_count
            self._write_success_total += 1
            self._retry_count = 0

            if self._publish_observation_entry:
                self._publish_observation_entry(batch)

            self.state = RecordingState.NORMAL_RECORDING
        else:
            self._retry_count += 1
            if self._retry_count >= self.MAX_RETRY_COUNT:
                # 丢弃这批数据，记录告警
                self._buffer = self._buffer[len(batch):]
                self._retry_count = 0
                self._log_event("BATCH_DISCARDED", {"count": len(batch), "reason": write_result.error_reason})
                self.state = RecordingState.NORMAL_RECORDING
            else:
                self.state = RecordingState.WRITE_PAUSED

        self._write_attempt_total += 1

    def _execute_batch_write(self, batch: List[BehaviorObservationEntry]) -> BatchWriteResult:
        # 获取写入令牌
        token = self._query_write_token() if self._query_write_token else None
        if token is None or token.authorized_slot_id != self._active_slot_id:
            return BatchWriteResult(success=False, error_reason="无法获取有效写入令牌")

        # 检查令牌是否过期
        if time.time() > token.expires_at:
            return BatchWriteResult(success=False, error_reason="写入令牌已过期")

        # 模拟写入（实际通过总线发送写入请求）
        if self._publish_batch_write_request:
            self._publish_batch_write_request(self._active_slot_id, batch, token.token_id)

        return BatchWriteResult(success=True, written_count=len(batch))

    def _retry_write(self):
        if self._buffer:
            self._perform_batch_write()

    def get_state(self) -> RecordingState:
        return self.state

    def emergency_shutdown(self):
        self.state = RecordingState.SYSTEM_PAUSED
        # 尝试强制写入缓冲区
        if self._buffer:
            self._perform_batch_write()
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
    print("  Agent-mlnf-mem 用户行为观测记录单元 (ag-mem-07) 演示")
    print("=" * 70)

    recorder = BehaviorObservationRecorder()
    recorder.set_active_slot_query(lambda: ActiveSlotInfo(
        session_id="S001", slot_id="SLOT-LONG-0001", user_id="U001"
    ))
    recorder.set_write_token_query(lambda: WriteToken(
        token_id="TOKEN-001", authorized_slot_id="SLOT-LONG-0001",
        expires_at=time.time() + 300
    ))

    print_separator("STEP 1: 记录文本输入行为")
    recorder.set_interaction_event_query(lambda: UserInteractionEvent(
        session_id="S001",
        event_type=BehaviorType.TEXT_INPUT,
        event_params={"length": 50, "language": "zh"}
    ))
    recorder.run_observation_cycle()
    print(f"  状态: {recorder.state.value}")
    print(f"  缓冲区条目数: {len(recorder._buffer)}")

    print_separator("STEP 2: 记录工具调用行为")
    recorder.set_interaction_event_query(lambda: UserInteractionEvent(
        session_id="S001",
        event_type=BehaviorType.TOOL_INVOKE,
        event_params={"tool_name": "weather_api", "complexity": 3}
    ))
    recorder.run_observation_cycle()
    print(f"  缓冲区条目数: {len(recorder._buffer)}")

    print_separator("STEP 3: 记录正向反馈（关联工具调用）")
    recorder.set_interaction_event_query(lambda: UserInteractionEvent(
        session_id="S001",
        event_type=BehaviorType.FEEDBACK_LIKE,
        event_params={"target": "weather_api_result"}
    ))
    recorder.run_observation_cycle()
    # 查看最后一条的关联ID
    if recorder._buffer:
        last = recorder._buffer[-1]
        print(f"  行为类型: {last.behavior_type.value}")
        print(f"  关联条目ID: {last.related_entry_id or '无'}")

    print("\n✅ 用户行为观测记录单元演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("ag-mem-07 用户行为观测记录单元 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        def setup_recorder():
            r = BehaviorObservationRecorder()
            r.set_active_slot_query(lambda: ActiveSlotInfo(
                session_id="S_TEST", slot_id="SLOT-LONG-0001", user_id="U_TEST"
            ))
            r.set_write_token_query(lambda: WriteToken(
                token_id="TOKEN-TEST", authorized_slot_id="SLOT-LONG-0001",
                expires_at=time.time() + 300
            ))
            r.run_observation_cycle()
            return r

        # TC-M07-01: 正常记录行为
        print("\n[TC-M07-01] 正常记录行为")
        try:
            r = setup_recorder()
            r.set_interaction_event_query(lambda: UserInteractionEvent(
                session_id="S_TEST", event_type=BehaviorType.TEXT_INPUT
            ))
            r.run_observation_cycle()
            assert len(r._buffer) == 1
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M07-02: 批量写入触发
        print("\n[TC-M07-02] 批量写入触发（达到20条阈值）")
        try:
            r = setup_recorder()
            for i in range(20):
                r.set_interaction_event_query(lambda: UserInteractionEvent(
                    session_id="S_TEST", event_type=BehaviorType.TEXT_INPUT
                ))
                r.run_observation_cycle()
            assert len(r._buffer) == 0  # 已触发批量写入清空
            assert r._total_recorded > 0
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M07-03: 无活跃槽位时忽略事件
        print("\n[TC-M07-03] 无活跃槽位时忽略事件")
        try:
            r = BehaviorObservationRecorder()
            r.set_active_slot_query(lambda: None)
            r.set_interaction_event_query(lambda: UserInteractionEvent(event_type=BehaviorType.TEXT_INPUT))
            r.run_observation_cycle()
            assert r.state == RecordingState.WAITING_SESSION
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M07-04: 关联前置TOOL_INVOKE
        print("\n[TC-M07-04] 关联前置TOOL_INVOKE")
        try:
            r = setup_recorder()
            r.set_interaction_event_query(lambda: UserInteractionEvent(event_type=BehaviorType.TOOL_INVOKE))
            r.run_observation_cycle()
            r.set_interaction_event_query(lambda: UserInteractionEvent(event_type=BehaviorType.FEEDBACK_LIKE))
            r.run_observation_cycle()
            last = r._buffer[-1] if r._buffer else None
            assert last is not None
            assert last.related_entry_id is not None
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M07-05: 定时批量写入
        print("\n[TC-M07-05] 定时批量写入（超过5秒）")
        try:
            r = setup_recorder()
            r.set_interaction_event_query(lambda: UserInteractionEvent(event_type=BehaviorType.TEXT_INPUT))
            r.run_observation_cycle()
            r._last_batch_time = 0  # 强制触发定时写入
            r.run_observation_cycle()
            assert r.state != RecordingState.WRITE_PAUSED
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M07-06: 紧急熔断
        print("\n[TC-M07-06] 紧急熔断")
        try:
            r = setup_recorder()
            r.emergency_shutdown()
            assert r.state == RecordingState.SYSTEM_PAUSED
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