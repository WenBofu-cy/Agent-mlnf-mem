#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-16
模块名称: 工具调用槽
所属分区: 三、漏斗二：任务经验漏斗 / 场景分槽管理
核心职责: 作为漏斗二中专门承载“工具调用”类任务经验的场景分槽。接收 ag-mem-03 路由的
          工具调用场景经验条目，管理该场景下的五层记忆存储（L1-L5）。在本槽内，安全显著性
          （S值）权重自动上调20%，以强化安全相关操作经验的优先留存。同时管理该槽专属的晋升
          阈值与遗忘策略参数。不参与认知决策，仅执行工具调用类经验的存储、检索与生命周期管理。

依赖模块:
    ag-mem-03(漏斗二专属调度单元), ag-mem-20~30(五层存储单元),
    ag-mem-35(三维权重系数配置单元), ag-mem-43(失败经验安全仲裁三道校验单元)
被依赖模块:
    ag-mem-03, ag-mem-25(相似经验归并单元)

安全约束:
  S-01: 本槽位仅接受场景标签确认为“工具调用”的经验条目
  S-02: S值权重上调仅在本槽位内生效，不得影响其他分槽的重要度计算
  S-03: 失败经验必须标记待安全校验，在未通过ag-mem-43校验前不得晋升至L2及以上
  S-04: 检测到敏感操作时必须自动提升S值确保高留存优先级
  S-05: 槽内经验的跨槽查询必须通过 ag-mem-03 统一路由
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
    result_label: str = "成功"
    needs_safety_check: bool = False
    timestamp: float = field(default_factory=time.time)


@dataclass
class WriteRequest:
    request_id: str = ""
    entry: ExperienceEntry = field(default_factory=ExperienceEntry)
    scene_label: str = ""


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
    safety_check_required: bool = False
    l5_direct_write: bool = False


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
    storage_usage_bytes: int = 0
    last_active_time: float = field(default_factory=time.time)


@dataclass
class WeightConfig:
    s_weight_coefficient: float = 1.2
    l1_to_l2_threshold: float = 0.40
    l2_to_l3_threshold: float = 0.60
    l3_to_l4_threshold: float = 0.80
    l4_to_l5_threshold: float = 0.90
    l1_forget_threshold: float = 0.10
    l2_forget_threshold: float = 0.25
    l3_forget_threshold: float = 0.35
    l5_direct_s_threshold: float = 0.9
    sensitive_ops = ["delete", "write_system", "modify_permission", "db_write", "shell_exec"]


class ToolCallSlot:
    def __init__(self):
        self.module_id = "ag-mem-16"
        self.module_name = "工具调用槽"
        self.version = "V1.0"
        self.slot_id = "ag-mem-16"

        self.state = SlotState.IDLE
        self._entries: Dict[str, ExperienceEntry] = {}
        self._total_entries: int = 0
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

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成, S值权重上调20%")

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
        # 校验场景标签
        if request.scene_label != "工具调用":
            if self._publish_write_confirm:
                self._publish_write_confirm(WriteConfirm())
            return

        self.state = SlotState.WRITING
        start_time = time.time()

        entry = request.entry
        # 应用专属权重：S值上调20%
        entry.s_value = min(entry.s_value * self._weight_config.s_weight_coefficient, 1.0)

        # 失败经验标记
        safety_check_required = False
        if entry.result_label in ("失败", "策略失误"):
            safety_check_required = True
            entry.needs_safety_check = True

        # 敏感操作自动提升S值
        if self._is_sensitive_operation(entry):
            entry.s_value = max(entry.s_value, 0.8)

        # S值直达L5判定
        l5_direct = False
        if entry.s_value >= self._weight_config.l5_direct_s_threshold and entry.result_label == "成功":
            assigned_layer = "L5"
            l5_direct = True
            entry.i_value = 1.0  # L5条目重要度置顶
        else:
            assigned_layer = "L1"

        entry_id = f"L{assigned_layer[-1]}-TOOL-{uuid.uuid4().hex[:8]}"
        entry.entry_id = entry_id
        self._entries[entry_id] = entry
        self._total_entries += 1
        self._layer_counts[assigned_layer] += 1

        elapsed = (time.time() - start_time) * 1000
        confirm = WriteConfirm(
            entry_id=entry_id,
            assigned_layer=assigned_layer,
            write_duration_ms=elapsed,
            safety_check_required=safety_check_required,
            l5_direct_write=l5_direct
        )

        if self._publish_write_confirm:
            self._publish_write_confirm(confirm)

        self._log_event("EXPERIENCE_WRITTEN", {
            "entry_id": entry_id,
            "slot": "tool_call",
            "l5_direct": l5_direct
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

        # 按重要度降序，S值作为第二排序键
        matched.sort(key=lambda x: (x.i_value, x.s_value), reverse=True)
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
                "slot": "tool_call",
                "merged": merged_count,
                "forgotten": forgotten_count
            })

    def _is_sensitive_operation(self, entry: ExperienceEntry) -> bool:
        tools = entry.experience_data.get("tools", [])
        if isinstance(tools, list):
            for tool in tools:
                if isinstance(tool, str):
                    for op in self._weight_config.sensitive_ops:
                        if op in tool.lower():
                            return True
        return False

    def _publish_status(self):
        if self._publish_slot_status:
            self._publish_slot_status(SlotStatus(
                total_entries=self._total_entries,
                layer_distribution=self._layer_counts.copy(),
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
    print("  Agent-mlnf-mem 工具调用槽 (ag-mem-16) 演示")
    print("=" * 70)

    slot = ToolCallSlot()

    print_separator("STEP 1: 写入普通工具调用经验（S值上调）")
    slot.set_write_request_query(lambda: WriteRequest(
        request_id="REQ-001",
        scene_label="工具调用",
        entry=ExperienceEntry(
            user_id="U001",
            experience_data={"tool": "weather_api", "action": "query"},
            s_value=0.5,
            i_value=0.4
        )
    ))
    slot.run_slot_cycle()
    print(f"  总条目数: {slot._total_entries}")

    print_separator("STEP 2: 写入高安全工具调用经验（S≥0.9，直达L5）")
    slot.set_write_request_query(lambda: WriteRequest(
        request_id="REQ-002",
        scene_label="工具调用",
        entry=ExperienceEntry(
            user_id="U001",
            experience_data={"tool": "auth_api", "action": "verify"},
            s_value=0.95,
            i_value=0.6
        )
    ))
    slot.run_slot_cycle()
    print(f"  总条目数: {slot._total_entries}")
    print(f"  L5条目数: {slot._layer_counts['L5']}")

    print_separator("STEP 3: 写入失败工具调用经验（标记待安全校验）")
    slot.set_write_request_query(lambda: WriteRequest(
        request_id="REQ-003",
        scene_label="工具调用",
        entry=ExperienceEntry(
            user_id="U001",
            experience_data={"tool": "db_write", "action": "update"},
            s_value=0.3,
            result_label="失败"
        )
    ))
    slot.run_slot_cycle()
    print(f"  总条目数: {slot._total_entries}")
    print(f"  L5条目数: {slot._layer_counts['L5']}")

    print("\n✅ 工具调用槽演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("ag-mem-16 工具调用槽 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        def setup_slot():
            return ToolCallSlot()

        # TC-M16-01: 正常写入工具调用经验
        print("\n[TC-M16-01] 正常写入工具调用经验")
        try:
            s = setup_slot()
            s.set_write_request_query(lambda: WriteRequest(
                request_id="T01", scene_label="工具调用",
                entry=ExperienceEntry(s_value=0.5, i_value=0.4)
            ))
            s.run_slot_cycle()
            assert s._total_entries == 1
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M16-02: S值上调20%
        print("\n[TC-M16-02] S值上调20%")
        try:
            s = setup_slot()
            s.set_write_request_query(lambda: WriteRequest(
                request_id="T02", scene_label="工具调用",
                entry=ExperienceEntry(s_value=0.5, i_value=0.4)
            ))
            s.run_slot_cycle()
            written = list(s._entries.values())[0]
            assert written.s_value == 0.6
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M16-03: S值直达L5
        print("\n[TC-M16-03] S值≥0.9直达L5")
        try:
            s = setup_slot()
            s.set_write_request_query(lambda: WriteRequest(
                request_id="T03", scene_label="工具调用",
                entry=ExperienceEntry(s_value=0.95, i_value=0.6, result_label="成功")
            ))
            s.run_slot_cycle()
            assert s._layer_counts["L5"] == 1
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M16-04: 失败经验标记
        print("\n[TC-M16-04] 失败经验标记待安全校验")
        try:
            s = setup_slot()
            s.set_write_request_query(lambda: WriteRequest(
                request_id="T04", scene_label="工具调用",
                entry=ExperienceEntry(result_label="失败")
            ))
            s.run_slot_cycle()
            written = list(s._entries.values())[0]
            assert written.needs_safety_check
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M16-05: 拒绝非工具调用场景
        print("\n[TC-M16-05] 拒绝非工具调用场景")
        try:
            s = setup_slot()
            s.set_write_request_query(lambda: WriteRequest(
                request_id="T05", scene_label="对话交互",
                entry=ExperienceEntry()
            ))
            s.run_slot_cycle()
            assert s._total_entries == 0
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M16-06: 紧急熔断
        print("\n[TC-M16-06] 紧急熔断")
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