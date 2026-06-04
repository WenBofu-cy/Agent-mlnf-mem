#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-37
模块名称: 重要度增量定时刷新单元
所属分区: 三、漏斗二：任务经验漏斗 / 三维重要度计算引擎
核心职责: 作为漏斗二中重要度维护的定时驱动器，每24小时对全量经验条目执行一次I值增量刷新。
          协调 ag-mem-33（C值统计单元）重新计算所有条目的复用频次C值（因时间衰减持续变化），
          再协调 ag-mem-36（I值聚合单元）基于最新的C值及其他维度分值重新聚合I值。通过定期
          刷新，确保经验的重要度始终反映最新的调用状态与时间衰减趋势，防止长期未调用但仍有
          潜在价值的经验因C值自然衰减而被过早遗忘。不参与各维度分值计算，仅执行刷新任务的
          编排、分批调度与进度监控。

依赖模块:
    ag-mem-03(漏斗二专属调度单元), ag-mem-33(C值统计单元),
    ag-mem-36(I值聚合单元), ag-mem-35(三维权重系数配置单元)
被依赖模块:
    ag-mem-03, ag-mem-33, ag-mem-36

安全约束:
  R-01: 刷新过程不得修改经验条目的任何内容数据，仅触发数值更新（C值与I值）
  R-02: 每批条目数硬限制为500条，批次间隔默认200ms，防止批量操作冲击下游存储与计算模块
  R-03: 刷新过程中不得跳过L4/L5层条目，确保长期与核心经验的重要度也得到及时更新
  R-04: 手动刷新必须有1小时冷却时间，防止管理员误操作频繁触发全量刷新导致系统负载过高
  R-05: 刷新超时后已完成的批次结果有效，未完成批次在下次刷新周期自动补全
"""

from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
import time
import uuid
import math


class SchedulerState(Enum):
    IDLE = "idle"
    SCANNING = "scanning"
    C_REFRESHING = "c_refreshing"
    I_RECALC = "i_recalc"
    SYSTEM_PAUSED = "system_paused"


@dataclass
class EntryItem:
    entry_id: str = ""
    current_layer: str = ""
    source_slot_id: str = ""
    current_i: float = 0.0
    current_c: float = 0.0


@dataclass
class BatchResult:
    batch_number: int = 0
    phase: str = "C"                # 新增：区分 C 或 I 阶段，防止跨阶段混淆
    success: bool = True
    processed_count: int = 0
    error_entry_ids: List[str] = field(default_factory=list)


@dataclass
class RefreshProgress:
    total_batches: int = 0
    completed_batches: int = 0
    current_phase: str = ""
    estimated_remaining_seconds: float = 0.0


@dataclass
class RefreshCompleteReceipt:
    total_entries: int = 0
    c_updated: int = 0
    i_changed: int = 0
    error_entries: int = 0
    total_duration_ms: float = 0.0


@dataclass
class ManualRefreshRejectedNotice:
    reason: str = ""
    remaining_cooldown_seconds: float = 0.0
    timestamp: float = field(default_factory=time.time)


@dataclass
class ManualRefreshCommand:
    scope: str = "全量"
    priority: int = 1
    force: bool = False


@dataclass
class RefreshConfig:
    interval_hours: float = 24.0
    max_batch_size: int = 500
    batch_interval_ms: float = 200.0
    c_refresh_timeout_sec: float = 300.0
    i_recalc_timeout_sec: float = 600.0
    manual_cooldown_hours: float = 1.0


class IValueRefreshScheduler:
    def __init__(self):
        self.module_id = "ag-mem-37"
        self.module_name = "重要度增量定时刷新单元"
        self.version = "V1.0"

        self.state = SchedulerState.IDLE
        self._config = RefreshConfig()

        # 刷新状态
        self._last_refresh_complete_time: float = time.time()
        self._last_manual_refresh_time: float = 0.0
        self._all_entries: List[EntryItem] = []
        self._current_batch_index: int = 0
        self._total_batches: int = 0
        self._c_start_time: float = 0.0
        self._i_start_time: float = 0.0
        self._c_success_count: int = 0
        self._i_success_count: int = 0
        self._error_entries: List[str] = []
        self._last_progress_time: float = 0.0
        self._pending_logs: List[Dict[str, Any]] = []

        # 回调注入
        self._query_entry_list = None
        self._query_batch_result = None
        self._query_manual_command = None

        self._publish_c_batch_request = None
        self._publish_i_batch_request = None
        self._publish_refresh_complete = None
        self._publish_progress = None
        self._publish_manual_refresh_rejected = None
        self._publish_event_log = None

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成，刷新间隔={self._config.interval_hours}h")

    # ========== 回调注入 ==========
    def set_entry_list_query(self, callback: Callable[[], Optional[List[EntryItem]]]):
        self._query_entry_list = callback

    def set_batch_result_query(self, callback: Callable[[], Optional[BatchResult]]):
        self._query_batch_result = callback

    def set_manual_command_query(self, callback: Callable[[], Optional[ManualRefreshCommand]]):
        self._query_manual_command = callback

    def set_c_batch_request_publisher(self, callback: Callable[[List[str], int], None]):
        self._publish_c_batch_request = callback

    def set_i_batch_request_publisher(self, callback: Callable[[List[str], int], None]):
        self._publish_i_batch_request = callback

    def set_refresh_complete_publisher(self, callback: Callable[[RefreshCompleteReceipt], None]):
        self._publish_refresh_complete = callback

    def set_progress_publisher(self, callback: Callable[[RefreshProgress], None]):
        self._publish_progress = callback

    # 修复：补充缺失的 setter 方法
    def set_manual_refresh_rejected_publisher(self, callback: Callable[[ManualRefreshRejectedNotice], None]):
        self._publish_manual_refresh_rejected = callback

    def set_event_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_event_log = callback

    # ========== 主循环 ==========
    def run_scheduler_cycle(self):
        now = time.time()

        if self.state == SchedulerState.SYSTEM_PAUSED:
            return

        # 定时刷新检查
        if self.state == SchedulerState.IDLE:
            hours_since_last = (now - self._last_refresh_complete_time) / 3600.0
            if hours_since_last >= self._config.interval_hours:
                self._start_refresh_cycle("定时刷新")

        # 手动刷新检查
        manual_cmd = self._query_manual_command() if self._query_manual_command else None
        if manual_cmd and self.state == SchedulerState.IDLE:
            cooldown_remaining = self._config.manual_cooldown_hours * 3600 - (now - self._last_manual_refresh_time)
            if cooldown_remaining > 0:
                self._log_event("MANUAL_REFRESH_COOLDOWN", {
                    "reason": "冷却中",
                    "remaining_seconds": cooldown_remaining
                })
                if self._publish_manual_refresh_rejected:
                    self._publish_manual_refresh_rejected(ManualRefreshRejectedNotice(
                        reason="手动刷新冷却中",
                        remaining_cooldown_seconds=cooldown_remaining
                    ))
            else:
                self._last_manual_refresh_time = now
                self._start_refresh_cycle("手动刷新")

        # C值刷新阶段：接收批次结果
        if self.state == SchedulerState.C_REFRESHING:
            batch_result = self._query_batch_result() if self._query_batch_result else None
            # 修复：按阶段分发，忽略不属于当前阶段的结果
            if batch_result and batch_result.phase == "C":
                self._handle_c_batch_result(batch_result)
            elif batch_result and batch_result.phase != "C":
                self._log_event("PHASE_MISMATCH", {
                    "current_phase": "C",
                    "received_phase": batch_result.phase,
                    "batch_number": batch_result.batch_number
                })
            if now - self._c_start_time > self._config.c_refresh_timeout_sec:
                self._finalize_c_phase(timeout=True)

        # I值重算阶段：接收批次结果
        if self.state == SchedulerState.I_RECALC:
            batch_result = self._query_batch_result() if self._query_batch_result else None
            # 修复：按阶段分发，忽略不属于当前阶段的结果
            if batch_result and batch_result.phase == "I":
                self._handle_i_batch_result(batch_result)
            elif batch_result and batch_result.phase != "I":
                self._log_event("PHASE_MISMATCH", {
                    "current_phase": "I",
                    "received_phase": batch_result.phase,
                    "batch_number": batch_result.batch_number
                })
            if now - self._i_start_time > self._config.i_recalc_timeout_sec:
                self._finalize_refresh(timeout=True)

        # 定期进度上报
        if self.state in (SchedulerState.C_REFRESHING, SchedulerState.I_RECALC):
            if now - self._last_progress_time >= 30:
                self._publish_current_progress()
                self._last_progress_time = now

    # ========== 刷新流程 ==========
    def _start_refresh_cycle(self, reason: str):
        self.state = SchedulerState.SCANNING
        entries = self._query_entry_list() if self._query_entry_list else []
        if not entries:
            self.state = SchedulerState.IDLE
            return

        self._all_entries = entries
        self._total_batches = math.ceil(len(entries) / self._config.max_batch_size)
        self._current_batch_index = 0
        self._error_entries = []
        self._log_event("REFRESH_STARTED", {"reason": reason, "total_entries": len(entries)})

        self.state = SchedulerState.C_REFRESHING
        self._c_start_time = time.time()
        self._c_success_count = 0
        self._send_next_c_batch()

    def _send_next_c_batch(self):
        start = self._current_batch_index * self._config.max_batch_size
        end = min(start + self._config.max_batch_size, len(self._all_entries))
        batch_ids = [e.entry_id for e in self._all_entries[start:end]]
        if self._publish_c_batch_request:
            self._publish_c_batch_request(batch_ids, self._current_batch_index)

    def _handle_c_batch_result(self, result: BatchResult):
        # 批次号校验，防止过期结果污染当前周期
        if result.batch_number != self._current_batch_index:
            self._log_event("STALE_BATCH_RESULT", {
                "phase": "C",
                "expected": self._current_batch_index,
                "received": result.batch_number
            })
            return

        if result.success:
            self._c_success_count += result.processed_count
        else:
            self._error_entries.extend(result.error_entry_ids)

        self._current_batch_index += 1
        if self._current_batch_index < self._total_batches:
            time.sleep(self._config.batch_interval_ms / 1000.0)
            self._send_next_c_batch()
        else:
            self._finalize_c_phase()

    def _finalize_c_phase(self, timeout: bool = False):
        if timeout:
            self._log_event("C_REFRESH_TIMEOUT", {"completed_batches": self._current_batch_index})

        self.state = SchedulerState.I_RECALC
        self._current_batch_index = 0
        self._i_start_time = time.time()
        self._i_success_count = 0
        self._send_next_i_batch()

    def _send_next_i_batch(self):
        start = self._current_batch_index * self._config.max_batch_size
        end = min(start + self._config.max_batch_size, len(self._all_entries))
        batch_ids = [e.entry_id for e in self._all_entries[start:end]]
        if self._publish_i_batch_request:
            self._publish_i_batch_request(batch_ids, self._current_batch_index)

    def _handle_i_batch_result(self, result: BatchResult):
        # 批次号校验，防止过期结果污染当前周期
        if result.batch_number != self._current_batch_index:
            self._log_event("STALE_BATCH_RESULT", {
                "phase": "I",
                "expected": self._current_batch_index,
                "received": result.batch_number
            })
            return

        if result.success:
            self._i_success_count += result.processed_count
        else:
            self._error_entries.extend(result.error_entry_ids)

        self._current_batch_index += 1
        if self._current_batch_index < self._total_batches:
            time.sleep(self._config.batch_interval_ms / 1000.0)
            self._send_next_i_batch()
        else:
            self._finalize_refresh()

    def _finalize_refresh(self, timeout: bool = False):
        if timeout:
            self._log_event("I_RECALC_TIMEOUT", {"completed_batches": self._current_batch_index})

        total_duration = (time.time() - self._c_start_time) * 1000
        receipt = RefreshCompleteReceipt(
            total_entries=len(self._all_entries),
            c_updated=self._c_success_count,
            i_changed=self._i_success_count,
            error_entries=len(self._error_entries),
            total_duration_ms=total_duration
        )

        if self._publish_refresh_complete:
            self._publish_refresh_complete(receipt)

        self._last_refresh_complete_time = time.time()
        self.state = SchedulerState.IDLE
        self._log_event("REFRESH_COMPLETED", {"receipt": receipt})

    def _publish_current_progress(self):
        total = self._total_batches
        completed = self._current_batch_index
        remaining = max(0, total - completed)
        avg_batch_time = (self._config.batch_interval_ms / 1000.0) + 0.5
        phase = "C值刷新" if self.state == SchedulerState.C_REFRESHING else "I值重算"
        est = remaining * avg_batch_time
        if self._publish_progress:
            self._publish_progress(RefreshProgress(
                total_batches=total,
                completed_batches=completed,
                current_phase=phase,
                estimated_remaining_seconds=est
            ))

    # ========== 辅助 ==========
    def emergency_shutdown(self):
        self.state = SchedulerState.SYSTEM_PAUSED
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
    print("  Agent-mlnf-mem 重要度增量定时刷新单元 (ag-mem-37) 演示")
    print("=" * 70)

    sched = IValueRefreshScheduler()

    print_separator("STEP 1: 模拟定时触发（超过24小时）")
    sched._last_refresh_complete_time = 0
    sched.set_entry_list_query(lambda: [
        EntryItem("E1", "L2", "ag-mem-16", 0.6, 0.3),
        EntryItem("E2", "L3", "ag-mem-17", 0.7, 0.5),
        EntryItem("E3", "L4", "ag-mem-15", 0.8, 0.9),
    ])
    sched.set_c_batch_request_publisher(lambda ids, batch: None)
    sched.set_i_batch_request_publisher(lambda ids, batch: None)
    sched.run_scheduler_cycle()
    print(f"  状态: {sched.state.value}")
    print(f"  总批次: {sched._total_batches}")

    sched.set_batch_result_query(lambda: BatchResult(batch_number=0, phase="C", success=True, processed_count=3))
    sched.run_scheduler_cycle()
    print(f"  C值刷新完成，状态: {sched.state.value}")

    sched.set_batch_result_query(lambda: BatchResult(batch_number=0, phase="I", success=True, processed_count=3))
    sched.run_scheduler_cycle()
    print(f"  I值重算完成，状态: {sched.state.value}")

    print_separator("STEP 2: 手动刷新冷却拒绝")
    sched.set_manual_command_query(lambda: ManualRefreshCommand())
    sched._last_manual_refresh_time = time.time()
    sched.run_scheduler_cycle()
    print(f"  状态: {sched.state.value} (应仍为IDLE，被冷却拒绝)")

    print_separator("STEP 3: 跨阶段批次结果被正确丢弃")
    sched._last_refresh_complete_time = 0
    sched.run_scheduler_cycle()
    # C 阶段收到一个 I 阶段的结果
    sched.set_batch_result_query(lambda: BatchResult(batch_number=0, phase="I", success=True, processed_count=1))
    sched.run_scheduler_cycle()
    print(f"  当前批次索引: {sched._current_batch_index} (应仍为0，被阶段检查丢弃)")

    print("\n✅ 重要度增量定时刷新单元演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("ag-mem-37 重要度增量定时刷新单元 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        def setup_scheduler():
            s = IValueRefreshScheduler()
            s._last_refresh_complete_time = 0
            s.set_entry_list_query(lambda: [
                EntryItem("E1", "L2", "ag-mem-16", 0.6, 0.3),
                EntryItem("E2", "L3", "ag-mem-17", 0.7, 0.5),
            ])
            s.set_c_batch_request_publisher(lambda ids, batch: None)
            s.set_i_batch_request_publisher(lambda ids, batch: None)
            return s

        # TC-M37-01: 定时刷新触发
        print("\n[TC-M37-01] 定时刷新触发")
        try:
            s = setup_scheduler()
            s.run_scheduler_cycle()
            assert s.state == SchedulerState.C_REFRESHING
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M37-02: C值阶段完成进入I值阶段
        print("\n[TC-M37-02] C值阶段完成进入I值阶段")
        try:
            s = setup_scheduler()
            s.run_scheduler_cycle()
            s.set_batch_result_query(lambda: BatchResult(batch_number=0, phase="C", success=True, processed_count=2))
            s.run_scheduler_cycle()
            assert s.state == SchedulerState.I_RECALC
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M37-03: I值阶段完成刷新结束
        print("\n[TC-M37-03] I值阶段完成刷新结束")
        try:
            s = setup_scheduler()
            s.run_scheduler_cycle()
            s.set_batch_result_query(lambda: BatchResult(batch_number=0, phase="C", success=True, processed_count=2))
            s.run_scheduler_cycle()
            s.set_batch_result_query(lambda: BatchResult(batch_number=0, phase="I", success=True, processed_count=2))
            s.run_scheduler_cycle()
            assert s.state == SchedulerState.IDLE
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M37-04: 过期批次号被正确丢弃
        print("\n[TC-M37-04] 过期批次号被正确丢弃")
        try:
            s = setup_scheduler()
            s.run_scheduler_cycle()
            s._handle_c_batch_result(BatchResult(batch_number=99, phase="C", success=True, processed_count=1))
            assert s._current_batch_index == 0
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M37-05: 跨阶段结果被丢弃（C阶段收到I结果）
        print("\n[TC-M37-05] 跨阶段结果被丢弃（C阶段收到I结果）")
        try:
            s = setup_scheduler()
            s.run_scheduler_cycle()
            # 当前是 C 阶段，却收到 phase="I" 的结果
            s.set_batch_result_query(lambda: BatchResult(batch_number=0, phase="I", success=True, processed_count=1))
            s.run_scheduler_cycle()
            assert s._current_batch_index == 0  # 不应递增
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M37-06: 紧急熔断
        print("\n[TC-M37-06] 紧急熔断")
        try:
            s = setup_scheduler()
            s.emergency_shutdown()
            assert s.state == SchedulerState.SYSTEM_PAUSED
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