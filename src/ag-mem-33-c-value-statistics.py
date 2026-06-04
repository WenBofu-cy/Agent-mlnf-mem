#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-33
模块名称: 复用频次C值统计单元
所属分区: 三、漏斗二：任务经验漏斗 / 三维重要度计算引擎
核心职责: 统计漏斗二中各经验条目在同类任务场景下被成功调用并执行的次数，将复用频次
          归一化为C值（0.0–1.0）。C值反映该经验的实用性与可复用程度：被频繁成功复用
          的经验获得高C值，加速晋升；从未被调用或调用失败的条目获得低C值，逐渐被遗忘。
          C值作为三维重要度I值的关键组成部分，是驱动经验从"偶发行为"进化为"稳定技能"
          的核心动力。通过ag-mem-37的重要度增量定时刷新机制，定期全量更新所有条目的C值。
          不参与认知决策，仅执行复用频次的客观统计与归一化。

依赖模块:
    ag-mem-23(L2近期层热度统计单元), ag-mem-15~19(各场景分槽),
    ag-mem-36(综合重要度I值聚合计算单元), ag-mem-35(三维权重系数配置单元),
    ag-mem-37(重要度增量定时刷新单元)
被依赖模块:
    ag-mem-36, ag-mem-35, ag-mem-37

安全约束:
  C-01: C值计算仅基于调用频次与时间衰减，不得访问或修改经验条目的内容数据
  C-02: L5核心层条目的C值固定为1.0，编译期硬编码，不得通过任何接口修改
  C-03: C值异常波动告警阈值（单次变化>0.5）为保护机制，不得用于自动化决策
  C-04: 复用事件的去重窗口（300秒）为可配置参数，但不得设为0（禁用去重会导致统计失真）
"""

from typing import Dict, List, Optional, Any, Callable, Tuple
from dataclasses import dataclass, field
from enum import Enum
import time
import uuid
import math


class StatisticsState(Enum):
    IDLE = "idle"
    INCREMENTAL_UPDATING = "incremental_updating"
    FULL_REFRESHING = "full_refreshing"
    SYSTEM_PAUSED = "system_paused"


@dataclass
class ReuseEvent:
    entry_id: str = ""
    call_result: str = "成功"
    call_timestamp: float = field(default_factory=time.time)
    source_slot_id: str = ""
    context_summary: str = ""


@dataclass
class FullRefreshCommand:
    trigger_reason: str = "定时"
    time_window_days: int = 30
    timestamp: float = field(default_factory=time.time)


@dataclass
class L2HitFrequency:
    entry_id: str = ""
    total_hits: int = 0
    recent_7d_hits: int = 0
    recent_24h_hits: int = 0


@dataclass
class CValueRecord:
    entry_id: str = ""
    success_call_count: int = 0
    current_c_value: float = 0.0
    source_slot_id: str = ""
    current_layer: str = "L1"
    saturation_threshold: int = 10
    last_success_time: float = 0.0          # 上次成功调用的时间戳
    last_event_time: float = 0.0            # 上次收到事件的时间戳（用于去重）


@dataclass
class CValueUpdateConfirm:
    updated_count: int = 0
    new_c_values: Dict[str, float] = field(default_factory=dict)
    update_duration_ms: float = 0.0


@dataclass
class CValueAnomalyAlert:
    entry_id: str = ""
    anomaly_type: str = ""
    current_c_value: float = 0.0
    previous_c_value: float = 0.0


@dataclass
class CValueStatusReport:
    state: str = ""
    total_entries: int = 0
    avg_c_value: float = 0.0
    last_refresh_time: float = 0.0


class CValueStatistics:
    # C值计算核心参数
    DEFAULT_SATURATION_THRESHOLD = 10
    TIME_DECAY_LAMBDA = 0.01
    L4_TIME_DECAY_LAMBDA = 0.005
    DEDUP_WINDOW_SEC = 300              # 去重窗口300秒

    # 异常波动阈值
    ANOMALY_FLUCTUATION_THRESHOLD = 0.5

    # 各场景分槽的C值饱和阈值
    SLOT_SATURATION_THRESHOLD = {
        "ag-mem-15": 8,
        "ag-mem-16": 10,
        "ag-mem-17": 12,
        "ag-mem-18": 10,
        "ag-mem-19": 15,
    }

    STATUS_REPORT_INTERVAL_SEC = 120

    def __init__(self):
        self.module_id = "ag-mem-33"
        self.module_name = "复用频次C值统计单元"
        self.version = "V1.0"

        self.state = StatisticsState.IDLE
        self._c_value_store: Dict[str, CValueRecord] = {}
        self._last_refresh_time: float = 0.0
        self._last_status_time: float = time.time()
        self._pending_logs: List[Dict[str, Any]] = []

        # 回调注入
        self._query_reuse_event = None
        self._query_full_refresh_command = None
        self._query_l2_hit_frequency = None
        self._query_entry_list = None
        self._query_weight_config = None

        self._publish_update_confirm = None
        self._publish_anomaly_alert = None
        self._publish_status_report = None
        self._publish_event_log = None

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成, 去重窗口={self.DEDUP_WINDOW_SEC}s")

    # ========== 回调注入 ==========
    def set_reuse_event_query(self, callback: Callable[[], Optional[ReuseEvent]]):
        self._query_reuse_event = callback

    def set_full_refresh_command_query(self, callback: Callable[[], Optional[FullRefreshCommand]]):
        self._query_full_refresh_command = callback

    def set_l2_hit_frequency_query(self, callback: Callable[[], Optional[List[L2HitFrequency]]]):
        self._query_l2_hit_frequency = callback

    def set_entry_list_query(self, callback: Callable[[], Optional[List[Dict[str, Any]]]]):
        self._query_entry_list = callback

    def set_weight_config_query(self, callback: Callable[[], Optional[Dict[str, Any]]]):
        self._query_weight_config = callback

    def set_update_confirm_publisher(self, callback: Callable[[CValueUpdateConfirm], None]):
        self._publish_update_confirm = callback

    def set_anomaly_alert_publisher(self, callback: Callable[[CValueAnomalyAlert], None]):
        self._publish_anomaly_alert = callback

    def set_status_report_publisher(self, callback: Callable[[CValueStatusReport], None]):
        self._publish_status_report = callback

    def set_event_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_event_log = callback

    # ========== 主循环 ==========
    def run_statistics_cycle(self):
        now = time.time()

        if self.state == StatisticsState.SYSTEM_PAUSED:
            return

        # 定期状态上报
        if now - self._last_status_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._publish_status()
            self._last_status_time = now

        # 处理全量刷新指令
        refresh_cmd = self._query_full_refresh_command() if self._query_full_refresh_command else None
        if refresh_cmd and self.state == StatisticsState.IDLE:
            self._handle_full_refresh()
            return

        # 处理复用事件通知
        event = self._query_reuse_event() if self._query_reuse_event else None
        if event and self.state == StatisticsState.IDLE:
            self._handle_reuse_event(event)

    # ========== 增量更新 ==========
    def _handle_reuse_event(self, event: ReuseEvent):
        self.state = StatisticsState.INCREMENTAL_UPDATING
        now = time.time()

        # 获取或初始化记录
        if event.entry_id in self._c_value_store:
            record = self._c_value_store[event.entry_id]
        else:
            saturation = self.SLOT_SATURATION_THRESHOLD.get(
                event.source_slot_id, self.DEFAULT_SATURATION_THRESHOLD
            )
            record = CValueRecord(
                entry_id=event.entry_id,
                source_slot_id=event.source_slot_id,
                saturation_threshold=saturation
            )
            self._c_value_store[event.entry_id] = record

        # 去重检查（安全约束C-04）
        if record.last_event_time > 0 and (now - record.last_event_time) < self.DEDUP_WINDOW_SEC:
            # 在去重窗口内，忽略重复事件
            self.state = StatisticsState.IDLE
            return

        # 更新最后事件时间
        record.last_event_time = now

        # 根据调用结果更新
        if event.call_result == "成功":
            record.success_call_count += 1
            record.last_success_time = now
        else:
            # 失败：不增加成功计数，但时间衰减继续累积
            # 如果从未成功过，以事件时间作为初始基准
            if record.last_success_time == 0.0:
                record.last_success_time = now

        # 重新计算C值
        previous_c = record.current_c_value
        new_c = self._calculate_c_value(record)
        record.current_c_value = new_c

        # 异常波动检测
        if abs(new_c - previous_c) > self.ANOMALY_FLUCTUATION_THRESHOLD and previous_c > 0:
            if self._publish_anomaly_alert:
                self._publish_anomaly_alert(CValueAnomalyAlert(
                    entry_id=event.entry_id,
                    anomaly_type="C值异常波动",
                    current_c_value=new_c,
                    previous_c_value=previous_c
                ))

        # 发送更新确认
        if self._publish_update_confirm:
            self._publish_update_confirm(CValueUpdateConfirm(
                updated_count=1,
                new_c_values={event.entry_id: new_c}
            ))

        self.state = StatisticsState.IDLE

    # ========== 全量刷新 ==========
    def _handle_full_refresh(self):
        self.state = StatisticsState.FULL_REFRESHING
        start_time = time.time()
        now = time.time()

        entries = self._query_entry_list() if self._query_entry_list else []
        if not entries:
            self.state = StatisticsState.IDLE
            return

        l2_hits = self._query_l2_hit_frequency() if self._query_l2_hit_frequency else []
        l2_hit_map = {h.entry_id: h for h in l2_hits}

        updated = 0
        new_c_values = {}

        for entry in entries:
            entry_id = entry.get("entry_id", "")
            current_layer = entry.get("current_layer", "L1")
            source_slot_id = entry.get("source_slot_id", "ag-mem-19")

            # L5层C值固定为1.0
            if current_layer == "L5":
                if entry_id in self._c_value_store:
                    self._c_value_store[entry_id].current_c_value = 1.0
                else:
                    self._c_value_store[entry_id] = CValueRecord(
                        entry_id=entry_id,
                        success_call_count=10,
                        current_c_value=1.0,
                        source_slot_id=source_slot_id,
                        current_layer="L5",
                        saturation_threshold=10,
                        last_success_time=now
                    )
                new_c_values[entry_id] = 1.0
                updated += 1
                continue

            # 获取或初始化记录
            if entry_id in self._c_value_store:
                record = self._c_value_store[entry_id]
            else:
                saturation = self.SLOT_SATURATION_THRESHOLD.get(
                    source_slot_id, self.DEFAULT_SATURATION_THRESHOLD
                )
                record = CValueRecord(
                    entry_id=entry_id,
                    source_slot_id=source_slot_id,
                    current_layer=current_layer,
                    saturation_threshold=saturation,
                    success_call_count=entry.get("cumulative_success_count", 0)
                )
                self._c_value_store[entry_id] = record

            # L2层条目额外使用热度统计修正
            if current_layer == "L2" and entry_id in l2_hit_map:
                hit_data = l2_hit_map[entry_id]
                record.success_call_count = max(record.success_call_count, hit_data.recent_7d_hits)

            # 更新层级
            record.current_layer = current_layer

            # 重新计算C值（基于实际时间戳，而非固定+1）
            previous_c = record.current_c_value
            new_c = self._calculate_c_value(record)
            record.current_c_value = new_c

            # 异常检测
            if abs(new_c - previous_c) > self.ANOMALY_FLUCTUATION_THRESHOLD and previous_c > 0:
                if self._publish_anomaly_alert:
                    self._publish_anomaly_alert(CValueAnomalyAlert(
                        entry_id=entry_id,
                        anomaly_type="C值异常波动",
                        current_c_value=new_c,
                        previous_c_value=previous_c
                    ))

            new_c_values[entry_id] = new_c
            updated += 1

        self._last_refresh_time = now
        elapsed = (time.time() - start_time) * 1000

        if self._publish_update_confirm:
            self._publish_update_confirm(CValueUpdateConfirm(
                updated_count=updated,
                new_c_values=new_c_values,
                update_duration_ms=elapsed
            ))

        self.state = StatisticsState.IDLE

    # ========== C值计算 ==========
    def _calculate_c_value(self, record: CValueRecord) -> float:
        # L5固定为1.0（安全约束C-02）
        if record.current_layer == "L5":
            return 1.0

        # 基于真实时间戳计算距上次成功的天数
        now = time.time()
        if record.last_success_time > 0:
            days_since_last_success = (now - record.last_success_time) / 86400.0
        else:
            # 从未成功过，以0天计（尚未有衰减）
            days_since_last_success = 0.0

        # 调用次数归一化
        threshold = record.saturation_threshold
        if record.success_call_count >= threshold:
            call_norm = 1.0
        else:
            call_norm = record.success_call_count / threshold

        # 时间衰减因子
        if record.current_layer == "L4":
            decay_lambda = self.L4_TIME_DECAY_LAMBDA
        else:
            decay_lambda = self.TIME_DECAY_LAMBDA

        decay_factor = math.exp(-decay_lambda * days_since_last_success)

        # C值 = 调用归一化 × 时间衰减
        c_value = call_norm * decay_factor
        return round(min(max(c_value, 0.0), 1.0), 2)

    # ========== 查询接口 ==========
    def get_c_value(self, entry_id: str) -> float:
        if entry_id in self._c_value_store:
            # 动态计算，确保返回最新的衰减后C值
            return self._calculate_c_value(self._c_value_store[entry_id])
        return 0.0

    def get_record(self, entry_id: str) -> Optional[CValueRecord]:
        return self._c_value_store.get(entry_id)

    # ========== 辅助方法 ==========
    def _publish_status(self):
        if not self._c_value_store:
            return
        avg_c = sum(r.current_c_value for r in self._c_value_store.values()) / len(self._c_value_store)
        if self._publish_status_report:
            self._publish_status_report(CValueStatusReport(
                state=self.state.value,
                total_entries=len(self._c_value_store),
                avg_c_value=round(avg_c, 3),
                last_refresh_time=self._last_refresh_time
            ))

    def get_state(self) -> StatisticsState:
        return self.state

    def emergency_shutdown(self):
        self.state = StatisticsState.SYSTEM_PAUSED
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
    print("  Agent-mlnf-mem 复用频次C值统计单元 (ag-mem-33) 演示")
    print("=" * 70)

    stats = CValueStatistics()

    print_separator("STEP 1: 首次成功调用 → C值=0.10")
    stats.set_reuse_event_query(lambda: ReuseEvent(
        entry_id="E01", call_result="成功", source_slot_id="ag-mem-16"
    ))
    stats.run_statistics_cycle()
    record = stats.get_record("E01")
    if record:
        print(f"  成功调用次数: {record.success_call_count}, C值={record.current_c_value}")

    print_separator("STEP 2: 去重窗口内重复调用 → 被忽略")
    stats.set_reuse_event_query(lambda: ReuseEvent(
        entry_id="E01", call_result="成功", source_slot_id="ag-mem-16"
    ))
    stats.run_statistics_cycle()
    record = stats.get_record("E01")
    if record:
        print(f"  成功调用次数: {record.success_call_count} (应仍为1), C值={record.current_c_value}")

    print_separator("STEP 3: 失败调用 → 计数不变但时间衰减推进")
    stats.set_reuse_event_query(lambda: ReuseEvent(
        entry_id="E01", call_result="失败", source_slot_id="ag-mem-16"
    ))
    stats.run_statistics_cycle()
    record = stats.get_record("E01")
    if record:
        print(f"  成功调用次数: {record.success_call_count} (未增加), C值={record.current_c_value}")

    print_separator("STEP 4: L5层条目C值固定为1.0")
    stats._c_value_store["L5-001"] = CValueRecord(
        entry_id="L5-001", success_call_count=1, current_layer="L5", last_success_time=time.time()
    )
    c5 = stats.get_c_value("L5-001")
    print(f"  L5条目C值: {c5}")

    print("\n✅ 复用频次C值统计单元演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("ag-mem-33 复用频次C值统计单元 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        def setup_stats():
            return CValueStatistics()

        # TC-M33-01: 首次成功调用
        print("\n[TC-M33-01] 首次成功调用")
        try:
            s = setup_stats()
            s.set_reuse_event_query(lambda: ReuseEvent(
                entry_id="T01", call_result="成功", source_slot_id="ag-mem-16"
            ))
            s.run_statistics_cycle()
            record = s.get_record("T01")
            assert record.success_call_count == 1
            assert record.current_c_value == 0.1
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M33-02: 去重窗口内重复调用被忽略
        print("\n[TC-M33-02] 去重窗口内重复调用被忽略")
        try:
            s = setup_stats()
            s.set_reuse_event_query(lambda: ReuseEvent(
                entry_id="T02", call_result="成功", source_slot_id="ag-mem-16"
            ))
            s.run_statistics_cycle()
            # 立即再次调用（去重窗口内）
            s.set_reuse_event_query(lambda: ReuseEvent(
                entry_id="T02", call_result="成功", source_slot_id="ag-mem-16"
            ))
            s.run_statistics_cycle()
            record = s.get_record("T02")
            assert record.success_call_count == 1  # 应为1，第二次被去重
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M33-03: 失败调用不增加计数
        print("\n[TC-M33-03] 失败调用不增加计数")
        try:
            s = setup_stats()
            s.set_reuse_event_query(lambda: ReuseEvent(
                entry_id="T03", call_result="失败", source_slot_id="ag-mem-16"
            ))
            s.run_statistics_cycle()
            record = s.get_record("T03")
            assert record.success_call_count == 0
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M33-04: L5层C值固定为1.0
        print("\n[TC-M33-04] L5层C值固定为1.0")
        try:
            s = setup_stats()
            s._c_value_store["L5-001"] = CValueRecord(
                entry_id="L5-001", success_call_count=1, current_layer="L5", last_success_time=time.time()
            )
            c = s.get_c_value("L5-001")
            assert c == 1.0
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M33-05: 达到饱和阈值
        print("\n[TC-M33-05] 达到饱和阈值（10次成功调用）")
        try:
            s = setup_stats()
            # 手动构造已饱和的记录
            record = CValueRecord(
                entry_id="T05", success_call_count=10, source_slot_id="ag-mem-16",
                saturation_threshold=10, last_success_time=time.time()
            )
            s._c_value_store["T05"] = record
            c = s.get_c_value("T05")
            assert c == 1.0
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M33-06: 紧急熔断
        print("\n[TC-M33-06] 紧急熔断")
        try:
            s = setup_stats()
            s.emergency_shutdown()
            assert s.state == StatisticsState.SYSTEM_PAUSED
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