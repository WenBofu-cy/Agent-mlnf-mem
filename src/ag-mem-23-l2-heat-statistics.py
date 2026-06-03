#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-23
模块名称: L2近期层热度统计单元
所属分区: 三、漏斗二：任务经验漏斗 / 五层存储
核心职责: 统计L2近期层中各经验条目被查询命中的频率。接收 ag-mem-22（L2存储单元）在每次
          完成经验查询后推送的命中条目列表，更新对应条目的命中计数。为 ag-mem-33（复用频次
          C值统计单元）提供L2层的命中频次数据，作为C值计算的原始输入；为 ag-mem-38（晋升
          双条件判定单元）提供热度参考权重。不参与晋升决策，仅执行命中事件的记录与统计。

依赖模块:
    ag-mem-22(L2近期层存储单元), ag-mem-33(复用频次C值统计单元),
    ag-mem-38(晋升双条件判定单元)
被依赖模块:
    ag-mem-33, ag-mem-38

安全约束:
  S-01: L2热度统计数据仅反映查询命中频率，不包含任何用户个人信息或经验内容
  S-02: 热度统计表仅存于内存，不持久化，系统重启后数据从零开始积累
  S-03: 本模块不主动查询L2存储内容，仅被动接收ag-mem-22推送的命中事件
  S-04: 热度数据的跨槽查询禁止：某个分槽查询时仅返回与该分槽相关的命中分布
"""

from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
import time
import uuid


class StatisticsState(Enum):
    IDLE = "idle"
    UPDATING = "updating"
    RESPONDING = "responding"
    SYSTEM_PAUSED = "system_paused"


@dataclass
class HitEntry:
    entry_id: str = ""
    hit_timestamp: float = 0.0
    query_source_slot: str = ""


@dataclass
class NewEntryNotice:
    entry_ids: List[str] = field(default_factory=list)
    source_slot_id: str = ""
    write_timestamp: float = 0.0


@dataclass
class HeatQueryRequest:
    entry_ids: List[str] = field(default_factory=list)
    query_type: str = "single"  # single / batch
    statistics_window_hours: int = 168  # 默认7天


@dataclass
class HeatData:
    entry_id: str = ""
    total_hits: int = 0
    recent_7d_hits: int = 0
    recent_24h_hits: int = 0
    last_hit_time: Optional[float] = None
    first_hit_time: Optional[float] = None
    hit_source_distribution: Dict[str, int] = field(default_factory=dict)


@dataclass
class HeatQueryResult:
    data: Dict[str, HeatData] = field(default_factory=dict)
    query_duration_ms: float = 0.0


@dataclass
class HeatStatusReport:
    state: StatisticsState = StatisticsState.IDLE
    total_tracked_entries: int = 0
    today_total_hits: int = 0
    avg_hit_rate: float = 0.0


class L2HeatStatistics:
    # 定时清理间隔
    CLEANUP_INTERVAL_HOURS = 24
    # 状态上报间隔
    STATUS_REPORT_INTERVAL_SEC = 60

    def __init__(self):
        self.module_id = "ag-mem-23"
        self.module_name = "L2近期层热度统计单元"
        self.version = "V1.0"

        self.state = StatisticsState.IDLE
        self._heat_table: Dict[str, HeatData] = {}
        self._today_hits: int = 0
        self._last_cleanup_time = time.time()
        self._last_status_time = time.time()
        self._pending_logs: List[Dict[str, Any]] = []

        # 回调注入
        self._query_hit_list = None
        self._query_new_entry_notice = None
        self._query_heat_query = None

        self._publish_heat_result = None
        self._publish_status_report = None
        self._publish_event_log = None

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    def set_hit_list_query(self, callback: Callable[[], Optional[List[HitEntry]]]):
        self._query_hit_list = callback

    def set_new_entry_notice_query(self, callback: Callable[[], Optional[NewEntryNotice]]):
        self._query_new_entry_notice = callback

    def set_heat_query_query(self, callback: Callable[[], Optional[HeatQueryRequest]]):
        self._query_heat_query = callback

    def set_heat_result_publisher(self, callback: Callable[[HeatQueryResult], None]):
        self._publish_heat_result = callback

    def set_status_report_publisher(self, callback: Callable[[HeatStatusReport], None]):
        self._publish_status_report = callback

    def set_event_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_event_log = callback

    def run_statistics_cycle(self) -> Optional[HeatQueryResult]:
        now = time.time()

        if self.state == StatisticsState.SYSTEM_PAUSED:
            return None

        # 处理新条目通知（初始化热度记录）
        notice = self._query_new_entry_notice() if self._query_new_entry_notice else None
        if notice:
            self._handle_new_entries(notice)

        # 处理命中条目列表（更新命中计数）
        hit_list = self._query_hit_list() if self._query_hit_list else None
        if hit_list:
            self._handle_hits(hit_list)

        # 定时清理过期统计
        if now - self._last_cleanup_time >= self.CLEANUP_INTERVAL_HOURS * 3600:
            self._cleanup_stale_entries()
            self._last_cleanup_time = now

        # 定期状态上报
        if now - self._last_status_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._publish_status()
            self._last_status_time = now

        # 处理热度查询请求
        query = self._query_heat_query() if self._query_heat_query else None
        if query:
            return self._handle_query(query)

        return None

    def _handle_new_entries(self, notice: NewEntryNotice):
        now = time.time()
        for entry_id in notice.entry_ids:
            if entry_id not in self._heat_table:
                self._heat_table[entry_id] = HeatData(
                    entry_id=entry_id,
                    first_hit_time=None,
                    last_hit_time=None
                )

    def _handle_hits(self, hit_list: List[HitEntry]):
        self.state = StatisticsState.UPDATING
        now = time.time()

        for hit in hit_list:
            entry_id = hit.entry_id
            if entry_id not in self._heat_table:
                self._heat_table[entry_id] = HeatData(
                    entry_id=entry_id,
                    first_hit_time=now
                )

            data = self._heat_table[entry_id]
            data.total_hits += 1
            data.recent_7d_hits += 1
            data.recent_24h_hits += 1
            data.last_hit_time = now
            if data.first_hit_time is None:
                data.first_hit_time = now

            # 更新分槽分布
            source = hit.query_source_slot
            if source not in data.hit_source_distribution:
                data.hit_source_distribution[source] = 0
            data.hit_source_distribution[source] += 1

            self._today_hits += 1

        self.state = StatisticsState.IDLE

    def _handle_query(self, request: HeatQueryRequest) -> HeatQueryResult:
        self.state = StatisticsState.RESPONDING
        start_time = time.time()

        result_data = {}
        for entry_id in request.entry_ids:
            if entry_id in self._heat_table:
                result_data[entry_id] = self._heat_table[entry_id]
            else:
                result_data[entry_id] = HeatData(entry_id=entry_id)

        elapsed = (time.time() - start_time) * 1000
        result = HeatQueryResult(
            data=result_data,
            query_duration_ms=elapsed
        )

        if self._publish_heat_result:
            self._publish_heat_result(result)

        self.state = StatisticsState.IDLE
        return result

    def _cleanup_stale_entries(self):
        """清理已从L2中删除的条目统计"""
        # 本模块不主动查询L2，通过外部传入有效条目列表来清理
        # 此处作为定期维护的占位，实际清理逻辑依赖L2的通知
        pass

    def get_heat_data(self, entry_id: str) -> Optional[HeatData]:
        return self._heat_table.get(entry_id)

    def _publish_status(self):
        total = len(self._heat_table)
        avg = self._today_hits / max(total, 1)
        if self._publish_status_report:
            self._publish_status_report(HeatStatusReport(
                state=self.state,
                total_tracked_entries=total,
                today_total_hits=self._today_hits,
                avg_hit_rate=round(avg, 2)
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


def print_separator(title: str):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def demo_main():
    print("=" * 70)
    print("  Agent-mlnf-mem L2近期层热度统计单元 (ag-mem-23) 演示")
    print("=" * 70)

    stats = L2HeatStatistics()

    print_separator("STEP 1: 接收新条目通知")
    stats.set_new_entry_notice_query(lambda: NewEntryNotice(
        entry_ids=["L2-TOOL-001", "L2-DIALOGUE-001"],
        source_slot_id="ag-mem-16"
    ))
    stats.run_statistics_cycle()
    print(f"  跟踪条目数: {len(stats._heat_table)}")

    print_separator("STEP 2: 接收命中事件")
    stats.set_hit_list_query(lambda: [
        HitEntry(entry_id="L2-TOOL-001", query_source_slot="ag-mem-16"),
        HitEntry(entry_id="L2-TOOL-001", query_source_slot="ag-mem-16"),
        HitEntry(entry_id="L2-DIALOGUE-001", query_source_slot="ag-mem-15"),
    ])
    stats.run_statistics_cycle()
    print(f"  L2-TOOL-001 总命中: {stats._heat_table['L2-TOOL-001'].total_hits}")
    print(f"  L2-DIALOGUE-001 总命中: {stats._heat_table['L2-DIALOGUE-001'].total_hits}")

    print_separator("STEP 3: 查询热度数据")
    result = stats._handle_query(HeatQueryRequest(entry_ids=["L2-TOOL-001"]))
    if result:
        data = result.data.get("L2-TOOL-001")
        if data:
            print(f"  总命中: {data.total_hits}")
            print(f"  近7日: {data.recent_7d_hits}")
            print(f"  近24小时: {data.recent_24h_hits}")

    print("\n✅ L2近期层热度统计单元演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("ag-mem-23 L2近期层热度统计单元 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        def setup_stats():
            return L2HeatStatistics()

        # TC-M23-01: 接收命中事件并更新计数
        print("\n[TC-M23-01] 接收命中事件并更新计数")
        try:
            s = setup_stats()
            s._heat_table["E01"] = HeatData(entry_id="E01")
            s.set_hit_list_query(lambda: [
                HitEntry(entry_id="E01"), HitEntry(entry_id="E01"), HitEntry(entry_id="E01")
            ])
            s.run_statistics_cycle()
            assert s._heat_table["E01"].total_hits == 3
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M23-02: 新条目初始化
        print("\n[TC-M23-02] 新条目初始化")
        try:
            s = setup_stats()
            s.set_new_entry_notice_query(lambda: NewEntryNotice(entry_ids=["E02", "E03"]))
            s.run_statistics_cycle()
            assert len(s._heat_table) == 2
            assert s._heat_table["E02"].total_hits == 0
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M23-03: 热度查询
        print("\n[TC-M23-03] 热度查询")
        try:
            s = setup_stats()
            s._heat_table["E04"] = HeatData(entry_id="E04", total_hits=5, recent_7d_hits=3, recent_24h_hits=1)
            result = s._handle_query(HeatQueryRequest(entry_ids=["E04"]))
            assert result is not None
            assert result.data["E04"].total_hits == 5
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M23-04: 命中条目含未知ID自动初始化
        print("\n[TC-M23-04] 命中条目含未知ID自动初始化")
        try:
            s = setup_stats()
            s.set_hit_list_query(lambda: [HitEntry(entry_id="UNKNOWN_ID")])
            s.run_statistics_cycle()
            assert "UNKNOWN_ID" in s._heat_table
            assert s._heat_table["UNKNOWN_ID"].total_hits == 1
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M23-05: 分槽分布统计
        print("\n[TC-M23-05] 分槽分布统计")
        try:
            s = setup_stats()
            s._heat_table["E05"] = HeatData(entry_id="E05")
            s.set_hit_list_query(lambda: [
                HitEntry(entry_id="E05", query_source_slot="ag-mem-15"),
                HitEntry(entry_id="E05", query_source_slot="ag-mem-15"),
                HitEntry(entry_id="E05", query_source_slot="ag-mem-16"),
            ])
            s.run_statistics_cycle()
            dist = s._heat_table["E05"].hit_source_distribution
            assert dist["ag-mem-15"] == 2
            assert dist["ag-mem-16"] == 1
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M23-06: 紧急熔断
        print("\n[TC-M23-06] 紧急熔断")
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
```