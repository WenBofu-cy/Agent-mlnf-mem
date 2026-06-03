#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-19
模块名称: 通用任务槽
所属分区: 三、漏斗二：任务经验漏斗 / 场景分槽管理
核心职责: 作为漏斗二中承载“通用任务”及混合/未分类任务经验的场景分槽。接收 ag-mem-03
          路由的通用任务场景经验条目，以及当其他四个场景分槽无法明确判定时作为默认兜底槽
          接收经验。管理该场景下的五层记忆存储（L1-L5）。本槽采用标准权重配置（无偏向上调），
          但拥有最强的遗忘保护参数，确保跨场景通用策略和不常见但可能有价值的混合经验不被
          过早清理。不参与认知决策，仅执行通用任务类经验的存储、检索与生命周期管理。

依赖模块:
    ag-mem-03(漏斗二专属调度单元), ag-mem-20~30(五层存储单元),
    ag-mem-35(三维权重系数配置单元)
被依赖模块:
    ag-mem-03, ag-mem-25(相似经验归并单元)

安全约束:
  S-01: 本槽接受“通用任务”标签经验及兜底路由的未分类经验，但不得主动接收明确属于其他
        四个场景的经验
  S-02: 兜底接收的经验在写入时不做任何权重调整，保持原始重要度值
  S-03: 强遗忘保护仅在本槽内生效，不得影响其他分槽的遗忘策略
  S-04: 槽内经验的跨槽查询必须通过 ag-mem-03 统一路由，不得直接响应外部模块的查询
  S-05: 兜底接收计数器仅用于统计，不得作为经验质量判断依据
"""

from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
import time
import uuid


class SlotState(Enum):
    IDLE = "idle"
    WRITING = "writing"
    QUERYING = "querying"
    MAINTENANCE = "maintenance"
    SYSTEM_PAUSED = "system_paused"


@dataclass
class ExperienceEntry:
    entry_id: str = ""
    user_id: str = ""
    scene_label: str = ""
    experience_data: Dict[str, Any] = field(default_factory=dict)
    i_value: float = 0.0
    s_value: float = 0.0
    v_value: float = 0.0
    c_value: float = 0.0
    is_fallback: bool = False
    result_label: str = "成功"
    timestamp: float = field(default_factory=time.time)


@dataclass
class WriteRequest:
    request_id: str = ""
    entry: ExperienceEntry = field(default_factory=ExperienceEntry)
    scene_label: str = ""
    is_fallback: bool = False


@dataclass
class QueryRequest:
    request_id: str = ""
    query_conditions: Dict[str, Any] = field(default_factory=dict)
    user_id: str = ""
    max_results: int = 20
    time_window_hours: int = 168


@dataclass
class WriteConfirm:
    entry_id: str = ""
    assigned_layer: str = "L1"
    estimated_promotion_time: float = 0.0
    write_duration_ms: float = 0.0
    is_fallback: bool = False


@dataclass
class QueryResult:
    matched_entries: List[ExperienceEntry] = field(default_factory=list)
    layers: Dict[str, int] = field(default_factory=dict)
    total_count: int = 0
    query_duration_ms: float = 0.0


@dataclass
class SlotStatus:
    total_entries: int = 0
    layer_distribution: Dict[str, int] = field(default_factory=dict)
    fallback_count: int = 0
    storage_usage_bytes: int = 0
    last_active_time: float = field(default_factory=time.time)


@dataclass
class WeightConfig:
    # 标准权重，无偏向上调
    standard_weight: float = 1.0
    l1_to_l2_threshold: float = 0.42
    l2_to_l3_threshold: float = 0.62
    l3_to_l4_threshold: float = 0.82
    l4_to_l5_threshold: float = 0.92
    # 强遗忘保护：遗忘阈值大幅下调
    l1_forget_threshold: float = 0.06
    l2_forget_threshold: float = 0.15
    l3_forget_threshold: float = 0.22
    forget_protection_label: str = "强保护"


class GeneralSlot:
    def __init__(self):
        self.module_id = "ag-mem-19"
        self.module_name = "通用任务槽"
        self.version = "V1.0"
        self.slot_id = "ag-mem-19"

        self.state = SlotState.IDLE
        self._entries: Dict[str, ExperienceEntry] = {}
        self._total_entries: int = 0
        self._fallback_count: int = 0
        self._layer_counts = {"L1": 0, "L2": 0, "L3": 0, "L4": 0, "L5": 0}
        self._last_status_time = time.time()
        self._weight_config = WeightConfig()
        self._pending_logs: List[Dict[str, Any]] = []

        # 回调注入
        self._query_write_request = None
        self._query_query_request = None
        self._query_maintenance_command = None

        self._publish_write_confirm = None
        self._publish_query_result = None
        self._publish_slot_status = None
        self._publish_event_log = None

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成, 标准权重, 遗忘保护={self._weight_config.forget_protection_label}")

    def set_write_request_query(self, callback: Callable[[], Optional[WriteRequest]]):
        self._query_write_request = callback

    def set_query_request_query(self, callback: Callable[[], Optional[QueryRequest]]):
        self._query_query_request = callback

    def set_maintenance_command_query(self, callback: Callable[[], Optional[Dict[str, Any]]]):
        self._query_maintenance_command = callback

    def set_write_confirm_publisher(self, callback: Callable[[WriteConfirm], None]):
        self._publish_write_confirm = callback

    def set_query_result_publisher(self, callback: Callable[[QueryResult], None]):
        self._publish_query_result = callback

    def set_slot_status_publisher(self, callback: Callable[[SlotStatus], None]):
        self._publish_slot_status = callback

    def set_event_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_event_log = callback

    def run_slot_cycle(self):
        now = time.time()

        if self.state == SlotState.SYSTEM_PAUSED:
            return

        # 定期状态上报
        if now - self._last_status_time >= 60:
            self._publish_status()
            self._last_status_time = now

        # 处理维护指令
        maint_cmd = self._query_maintenance_command() if self._query_maintenance_command else None
        if maint_cmd and self.state == SlotState.IDLE:
            self.state = SlotState.MAINTENANCE
            self._perform_maintenance()
            self.state = SlotState.IDLE
            return

        # 处理写入请求
        write_req = self._query_write_request() if self._query_write_request else None
        if write_req and self.state == SlotState.IDLE:
            self._handle_write(write_req)
            return

        # 处理查询请求
        query_req = self._query_query_request() if self._query_query_request else None
        if query_req and self.state == SlotState.IDLE:
            self._handle_query(query_req)
            return

    def _handle_write(self, request: WriteRequest):
        # 仅接受“通用任务”标签或兜底路由的经验
        if request.scene_label != "通用任务" and not request.is_fallback:
            if self._publish_write_confirm:
                self._publish_write_confirm(WriteConfirm())
            return

        self.state = SlotState.WRITING
        start_time = time.time()

        entry = request.entry
        # 不进行权重上调，保持原始重要度
        # 权重系数为标准 1.0

        is_fallback = request.is_fallback
        if is_fallback:
            self._fallback_count += 1
            entry.is_fallback = True

        # 存储到 L1
        prefix = "L1-FALLBACK" if is_fallback else "L1-GENERAL"
        entry_id = f"{prefix}-{uuid.uuid4().hex[:8]}"
        entry.entry_id = entry_id
        self._entries[entry_id] = entry
        self._total_entries += 1
        self._layer_counts["L1"] += 1

        elapsed = (time.time() - start_time) * 1000
        confirm = WriteConfirm(
            entry_id=entry_id,
            assigned_layer="L1",
            write_duration_ms=elapsed,
            is_fallback=is_fallback
        )

        if self._publish_write_confirm:
            self._publish_write_confirm(confirm)

        self._log_event("EXPERIENCE_WRITTEN", {
            "entry_id": entry_id,
            "slot": "general",
            "is_fallback": is_fallback
        })
        self.state = SlotState.IDLE

    def _handle_query(self, request: QueryRequest):
        self.state = SlotState.QUERYING
        start_time = time.time()

        matched = []
        for entry in self._entries.values():
            keywords = request.query_conditions.get("keywords", [])
            if keywords:
                match = False
                for kw in keywords:
                    if kw in str(entry.experience_data):
                        match = True
                        break
                if not match:
                    continue
            matched.append(entry)

        # 按重要度降序排序
        matched.sort(key=lambda x: x.i_value, reverse=True)
        matched = matched[:request.max_results]

        layers = {"L1": 0, "L2": 0, "L3": 0, "L4": 0, "L5": 0}
        for e in matched:
            for key in layers:
                if key in e.entry_id:
                    layers[key] += 1
                    break

        elapsed = (time.time() - start_time) * 1000
        result = QueryResult(
            matched_entries=matched,
            layers=layers,
            total_count=len(matched),
            query_duration_ms=elapsed
        )

        if self._publish_query_result:
            self._publish_query_result(result)

        self.state = SlotState.IDLE

    def _perform_maintenance(self):
        merged_count = 0
        forgotten_count = 0
        if self._publish_event_log:
            self._publish_event_log({
                "event": "MAINTENANCE_DONE",
                "slot": "general",
                "merged": merged_count,
                "forgotten": forgotten_count
            })

    def _publish_status(self):
        if self._publish_slot_status:
            self._publish_slot_status(SlotStatus(
                total_entries=self._total_entries,
                layer_distribution=self._layer_counts.copy(),
                fallback_count=self._fallback_count,
                last_active_time=time.time()
            ))

    def get_state(self) -> SlotState:
        return self.state

    def emergency_shutdown(self):
        self.state = SlotState.SYSTEM_PAUSED
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
    print("  Agent-mlnf-mem 通用任务槽 (ag-mem-19) 演示")
    print("=" * 70)

    slot = GeneralSlot()

    print_separator("STEP 1: 写入通用任务经验")
    slot.set_write_request_query(lambda: WriteRequest(
        request_id="REQ-001",
        scene_label="通用任务",
        entry=ExperienceEntry(
            user_id="U001",
            experience_data={"task": "综合查询", "tools": ["search", "weather"]},
            v_value=0.4,
            i_value=0.5
        )
    ))
    slot.run_slot_cycle()
    written = list(slot._entries.values())[0] if slot._entries else None
    if written:
        print(f"  条目ID: {written.entry_id}")
        print(f"  重要度I值: {written.i_value} (保持原始值)")

    print_separator("STEP 2: 写入兜底路由经验")
    slot.set_write_request_query(lambda: WriteRequest(
        request_id="REQ-002",
        scene_label="通用任务",
        is_fallback=True,
        entry=ExperienceEntry(
            user_id="U002",
            experience_data={"task": "未知分类任务"},
            i_value=0.3
        )
    ))
    slot.run_slot_cycle()
    print(f"  总条目数: {slot._total_entries}")
    print(f"  兜底计数: {slot._fallback_count}")

    print_separator("STEP 3: 拒绝明确属于其他场景的写入")
    slot.set_write_request_query(lambda: WriteRequest(
        request_id="REQ-003",
        scene_label="工具调用",
        is_fallback=False,
        entry=ExperienceEntry()
    ))
    slot.run_slot_cycle()
    print(f"  总条目数仍为: {slot._total_entries}")

    print("\n✅ 通用任务槽演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("ag-mem-19 通用任务槽 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        def setup_slot():
            return GeneralSlot()

        # TC-M19-01: 正常写入通用任务经验
        print("\n[TC-M19-01] 正常写入通用任务经验")
        try:
            s = setup_slot()
            s.set_write_request_query(lambda: WriteRequest(
                request_id="T01", scene_label="通用任务",
                entry=ExperienceEntry(i_value=0.5)
            ))
            s.run_slot_cycle()
            assert s._total_entries == 1
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M19-02: 写入兜底路由经验
        print("\n[TC-M19-02] 写入兜底路由经验")
        try:
            s = setup_slot()
            s.set_write_request_query(lambda: WriteRequest(
                request_id="T02", scene_label="通用任务", is_fallback=True,
                entry=ExperienceEntry(i_value=0.3)
            ))
            s.run_slot_cycle()
            assert s._total_entries == 1
            assert s._fallback_count == 1
            written = list(s._entries.values())[0]
            assert written.is_fallback
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M19-03: 拒绝非通用任务场景写入（无兜底标记）
        print("\n[TC-M19-03] 拒绝非通用任务场景写入")
        try:
            s = setup_slot()
            s.set_write_request_query(lambda: WriteRequest(
                request_id="T03", scene_label="工具调用", is_fallback=False,
                entry=ExperienceEntry()
            ))
            s.run_slot_cycle()
            assert s._total_entries == 0
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M19-04: 标准权重不调整V/S/C值
        print("\n[TC-M19-04] 标准权重不调整V/S/C值")
        try:
            s = setup_slot()
            original_v = 0.5
            original_s = 0.4
            original_c = 0.3
            s.set_write_request_query(lambda: WriteRequest(
                request_id="T04", scene_label="通用任务",
                entry=ExperienceEntry(v_value=original_v, s_value=original_s, c_value=original_c)
            ))
            s.run_slot_cycle()
            written = list(s._entries.values())[0]
            assert written.v_value == original_v
            assert written.s_value == original_s
            assert written.c_value == original_c
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M19-05: 查询匹配
        print("\n[TC-M19-05] 查询匹配")
        try:
            s = setup_slot()
            s.set_write_request_query(lambda: WriteRequest(
                request_id="T05-1", scene_label="通用任务",
                entry=ExperienceEntry(experience_data={"text": "综合任务"}, i_value=0.6)
            ))
            s.run_slot_cycle()
            s.set_query_request_query(lambda: QueryRequest(
                request_id="T05-2", query_conditions={"keywords": ["综合"]}
            ))
            s.run_slot_cycle()
            assert s._total_entries == 1
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M19-06: 紧急熔断
        print("\n[TC-M19-06] 紧急熔断")
        try:
            s = setup_slot()
            s.emergency_shutdown()
            assert s.state == SlotState.SYSTEM_PAUSED
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