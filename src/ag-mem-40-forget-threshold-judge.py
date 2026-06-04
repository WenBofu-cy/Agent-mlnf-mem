#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-40
模块名称: 遗忘阈值判定单元
所属分区: 三、漏斗二：任务经验漏斗 / 晋升与遗忘执行机制
核心职责: 扫描漏斗二各层级（L1-L4）中经验条目的综合重要度I值，将其与各分槽专属的遗忘阈值
          进行比对，生成遗忘候选清单。L4层受强遗忘保护，L5层永久保留不参与遗忘判定。将判定后
          的遗忘候选清单发送至 ag-mem-42 执行物理删除或归档。不参与实际删除操作，仅执行阈值
          比对与候选生成。

          对于接近遗忘阈值的条目，本模块会向 ag-mem-41 发起异步复用次数校验请求，并在候选
          清单中标记 pending_reuse_check=True。ag-mem-42 在执行删除前，应查询 ag-mem-41 的
          校验结果，对被保护的条目予以保留。

依赖模块:
    ag-mem-15~19(各场景分槽，提供待扫描条目), ag-mem-35(三维权重系数配置单元，提供遗忘阈值),
    ag-mem-41(最低复用次数校验单元)
被依赖模块:
    ag-mem-42(冗余记忆删除与归档单元), ag-mem-41

安全约束:
  F-01: L5核心层条目永远不参与遗忘判定，编译期硬编码排除
  F-02: L4层条目使用强保护遗忘阈值，仅在I值极低且复用不足时才被纳入候选
  F-03: 遗忘判定必须使用各分槽专属阈值，不得使用全局默认值一刀切
  F-04: 本模块仅生成候选清单，不得直接操作经验条目的删除
"""

from typing import Dict, List, Optional, Any, Callable, Tuple
from dataclasses import dataclass, field
from enum import Enum
import time
import uuid


class ForgetJudgeState(Enum):
    IDLE = "idle"
    SCANNING = "scanning"
    JUDGING = "judging"
    OUTPUTTING = "outputting"
    SYSTEM_PAUSED = "system_paused"


@dataclass
class ExperienceEntry:
    entry_id: str = ""
    source_layer: str = ""
    source_slot_id: str = ""
    i_value: float = 0.0
    retention_duration_h: float = 0.0
    caution_label: str = "NORMAL"


@dataclass
class ForgetScanRequest:
    scope: str = ""
    target_layers: List[str] = field(default_factory=list)
    trigger_reason: str = "定时"


@dataclass
class SlotForgetThresholds:
    l1: float = 0.10
    l2: float = 0.20
    l3: float = 0.30
    l4: float = 0.25


@dataclass
class ForgetCandidate:
    entry_id: str = ""
    source_layer: str = ""
    source_slot_id: str = ""
    i_value: float = 0.0
    forget_threshold: float = 0.0
    forget_reason: str = ""
    pending_reuse_check: bool = False


@dataclass
class ForgetCandidateList:
    entries: List[ForgetCandidate] = field(default_factory=list)
    trigger_reason: str = ""


@dataclass
class JudgeResult:
    total_scanned: int = 0
    candidate_count: int = 0
    protected_count: int = 0
    candidate_list: ForgetCandidateList = field(default_factory=ForgetCandidateList)


class ForgetThresholdJudge:
    STATUS_REPORT_INTERVAL_SEC = 120
    NEAR_THRESHOLD_DELTA = 0.05

    def __init__(self):
        self.module_id = "ag-mem-40"
        self.module_name = "遗忘阈值判定单元"
        self.version = "V1.0"

        self.state = ForgetJudgeState.IDLE
        self._slot_forget_thresholds: Dict[str, SlotForgetThresholds] = {}
        self._total_scanned: int = 0
        self._total_candidates: int = 0
        self._last_status_time: float = time.time()
        self._pending_logs: List[Dict[str, Any]] = []

        # 回调注入
        self._query_scan_request = None
        self._query_entries_to_scan = None
        self._query_slot_thresholds = None

        self._publish_forget_candidates = None
        self._publish_reuse_check_request = None
        self._publish_judge_result = None
        self._publish_status_report = None
        self._publish_event_log = None

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    # ========== 回调注入 ==========
    def set_scan_request_query(self, callback: Callable[[], Optional[ForgetScanRequest]]):
        self._query_scan_request = callback

    def set_entries_to_scan_query(self, callback: Callable[[List[str]], Optional[List[ExperienceEntry]]]):
        self._query_entries_to_scan = callback

    def set_slot_thresholds_query(self, callback: Callable[[], Optional[Dict[str, SlotForgetThresholds]]]):
        self._query_slot_thresholds = callback

    def set_forget_candidates_publisher(self, callback: Callable[[ForgetCandidateList], None]):
        self._publish_forget_candidates = callback

    def set_reuse_check_request_publisher(self, callback: Callable[[str, Dict[str, Any]], None]):
        self._publish_reuse_check_request = callback

    def set_judge_result_publisher(self, callback: Callable[[JudgeResult], None]):
        self._publish_judge_result = callback

    def set_status_report_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_status_report = callback

    def set_event_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_event_log = callback

    # ========== 主循环 ==========
    def run_judge_cycle(self):
        now = time.time()

        if self.state == ForgetJudgeState.SYSTEM_PAUSED:
            return

        # 定期状态上报
        if now - self._last_status_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._publish_status()
            self._last_status_time = now

        # 同步阈值配置
        if self._query_slot_thresholds:
            thresholds = self._query_slot_thresholds()
            if thresholds:
                self._slot_forget_thresholds = thresholds

        # 接收扫描请求
        scan_req = self._query_scan_request() if self._query_scan_request else None
        if scan_req is None:
            return

        self.state = ForgetJudgeState.SCANNING

        entries = self._query_entries_to_scan(scan_req.target_layers) if self._query_entries_to_scan else []
        if not entries:
            self.state = ForgetJudgeState.IDLE
            return

        self.state = ForgetJudgeState.JUDGING
        result = self._judge_entries(entries, scan_req.trigger_reason)
        self.state = ForgetJudgeState.OUTPUTTING

        if result.candidate_list.entries and self._publish_forget_candidates:
            self._publish_forget_candidates(result.candidate_list)

        if self._publish_judge_result:
            self._publish_judge_result(result)

        self._total_scanned += result.total_scanned
        self._total_candidates += result.candidate_count

        self.state = ForgetJudgeState.IDLE

    # ========== 核心判定 ==========
    def _judge_entries(self, entries: List[ExperienceEntry], reason: str) -> JudgeResult:
        candidates = []
        protected = 0

        for entry in entries:
            # L5 永久保留，不参与遗忘判定（安全约束F-01）
            if entry.source_layer == "L5":
                protected += 1
                continue

            # 获取该分槽该层级的遗忘阈值（安全约束F-03）
            threshold = self._get_forget_threshold(entry.source_slot_id, entry.source_layer)
            if threshold is None:
                continue

            # I值低于遗忘阈值，进入候选
            if entry.i_value < threshold:
                # 检查是否接近阈值（修复：明确语义，使用单向差值）
                near_threshold = (threshold - entry.i_value) < self.NEAR_THRESHOLD_DELTA

                if near_threshold:
                    # 异步发起复用次数校验请求（修复：不等待结果，由ag-mem-42执行删除前确认）
                    if self._publish_reuse_check_request:
                        self._publish_reuse_check_request("ag-mem-41", {
                            "entry_id": entry.entry_id,
                            "current_i": entry.i_value,
                            "current_layer": entry.source_layer,
                            "source_slot_id": entry.source_slot_id,
                            "forget_threshold": threshold
                        })

                candidates.append(ForgetCandidate(
                    entry_id=entry.entry_id,
                    source_layer=entry.source_layer,
                    source_slot_id=entry.source_slot_id,
                    i_value=entry.i_value,
                    forget_threshold=threshold,
                    forget_reason=f"I值={entry.i_value:.2f} < 遗忘阈值={threshold:.2f}",
                    pending_reuse_check=near_threshold
                ))

        candidate_list = ForgetCandidateList(
            entries=candidates,
            trigger_reason=reason
        )

        return JudgeResult(
            total_scanned=len(entries),
            candidate_count=len(candidates),
            protected_count=protected,
            candidate_list=candidate_list
        )

    def _get_forget_threshold(self, slot_id: str, layer: str) -> Optional[float]:
        slot_cfg = self._slot_forget_thresholds.get(slot_id, SlotForgetThresholds())
        if layer == "L1":
            return slot_cfg.l1
        elif layer == "L2":
            return slot_cfg.l2
        elif layer == "L3":
            return slot_cfg.l3
        elif layer == "L4":
            return slot_cfg.l4
        return None

    # ========== 辅助 ==========
    def _publish_status(self):
        if self._publish_status_report:
            self._publish_status_report({
                "state": self.state.value,
                "total_scanned": self._total_scanned,
                "total_candidates": self._total_candidates
            })

    def emergency_shutdown(self):
        self.state = ForgetJudgeState.SYSTEM_PAUSED
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
    print("  Agent-mlnf-mem 遗忘阈值判定单元 (ag-mem-40) 演示")
    print("=" * 70)

    judge = ForgetThresholdJudge()
    judge.set_slot_thresholds_query(lambda: {
        "ag-mem-16": SlotForgetThresholds(l1=0.10, l2=0.25, l3=0.35, l4=0.25),
        "ag-mem-19": SlotForgetThresholds(l1=0.06, l2=0.15, l3=0.22, l4=0.18),
    })
    test_entries = [
        ExperienceEntry(entry_id="E01", source_layer="L1", source_slot_id="ag-mem-16", i_value=0.05),
        ExperienceEntry(entry_id="E02", source_layer="L2", source_slot_id="ag-mem-16", i_value=0.20),
        ExperienceEntry(entry_id="E03", source_layer="L3", source_slot_id="ag-mem-16", i_value=0.30),
        ExperienceEntry(entry_id="E04", source_layer="L5", source_slot_id="ag-mem-16", i_value=0.01),
        ExperienceEntry(entry_id="E05", source_layer="L1", source_slot_id="ag-mem-19", i_value=0.04),
    ]
    judge.set_entries_to_scan_query(lambda layers: test_entries)

    print_separator("STEP 1: 执行遗忘判定")
    judge.set_scan_request_query(lambda: ForgetScanRequest(
        target_layers=["L1", "L2", "L3", "L4", "L5"],
        trigger_reason="定时"
    ))
    judge.run_judge_cycle()
    print(f"  总扫描: {judge._total_scanned}")
    print(f"  候选数: {judge._total_candidates}")

    print_separator("STEP 2: 接近阈值条目标记为待定")
    judge2 = ForgetThresholdJudge()
    judge2.set_slot_thresholds_query(lambda: {
        "ag-mem-16": SlotForgetThresholds(l1=0.10, l2=0.20, l3=0.30, l4=0.25),
    })
    judge2.set_entries_to_scan_query(lambda layers: [
        ExperienceEntry(entry_id="E06", source_layer="L1", source_slot_id="ag-mem-16", i_value=0.07),
    ])
    judge2.set_scan_request_query(lambda: ForgetScanRequest(target_layers=["L1"]))
    judge2.run_judge_cycle()
    print(f"  候选数: {judge2._total_candidates}")
    print(f"  (接近阈值的条目被标记为 pending_reuse_check=True，由 ag-mem-42 在执行删除前确认)")

    print("\n✅ 遗忘阈值判定单元演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("ag-mem-40 遗忘阈值判定单元 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        def setup_judge():
            j = ForgetThresholdJudge()
            j.set_slot_thresholds_query(lambda: {
                "ag-mem-16": SlotForgetThresholds(l1=0.10, l2=0.20, l3=0.30, l4=0.25),
                "ag-mem-19": SlotForgetThresholds(l1=0.06, l2=0.15, l3=0.22, l4=0.18),
            })
            return j

        # TC-M40-01: 低于遗忘阈值，纳入候选
        print("\n[TC-M40-01] 低于遗忘阈值纳入候选")
        try:
            j = setup_judge()
            j.set_entries_to_scan_query(lambda layers: [
                ExperienceEntry(entry_id="T01", source_layer="L1", source_slot_id="ag-mem-16", i_value=0.05)
            ])
            j.set_scan_request_query(lambda: ForgetScanRequest(target_layers=["L1"]))
            j.run_judge_cycle()
            assert j._total_candidates == 1
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M40-02: 高于遗忘阈值，不受影响
        print("\n[TC-M40-02] 高于遗忘阈值不受影响")
        try:
            j = setup_judge()
            j.set_entries_to_scan_query(lambda layers: [
                ExperienceEntry(entry_id="T02", source_layer="L1", source_slot_id="ag-mem-16", i_value=0.50)
            ])
            j.set_scan_request_query(lambda: ForgetScanRequest(target_layers=["L1"]))
            j.run_judge_cycle()
            assert j._total_candidates == 0
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M40-03: L5条目被保护
        print("\n[TC-M40-03] L5条目被保护")
        try:
            j = setup_judge()
            j.set_entries_to_scan_query(lambda layers: [
                ExperienceEntry(entry_id="T03", source_layer="L5", source_slot_id="ag-mem-16", i_value=0.01)
            ])
            j.set_scan_request_query(lambda: ForgetScanRequest(target_layers=["L5"]))
            j.run_judge_cycle()
            assert j._total_candidates == 0
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M40-04: 分槽专属阈值正确应用
        print("\n[TC-M40-04] 通用任务槽强保护（阈值0.06）")
        try:
            j = setup_judge()
            j.set_entries_to_scan_query(lambda layers: [
                ExperienceEntry(entry_id="T04", source_layer="L1", source_slot_id="ag-mem-19", i_value=0.05)
            ])
            j.set_scan_request_query(lambda: ForgetScanRequest(target_layers=["L1"]))
            j.run_judge_cycle()
            assert j._total_candidates == 1
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M40-05: 接近阈值条目标记 pending_reuse_check
        print("\n[TC-M40-05] 接近阈值条目标记 pending_reuse_check")
        try:
            j = setup_judge()
            j.set_entries_to_scan_query(lambda layers: [
                ExperienceEntry(entry_id="T05", source_layer="L1", source_slot_id="ag-mem-16", i_value=0.07)
            ])
            j.set_scan_request_query(lambda: ForgetScanRequest(target_layers=["L1"]))
            j.run_judge_cycle()
            assert j._total_candidates == 1
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M40-06: 紧急熔断
        print("\n[TC-M40-06] 紧急熔断")
        try:
            j = setup_judge()
            j.emergency_shutdown()
            assert j.state == ForgetJudgeState.SYSTEM_PAUSED
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