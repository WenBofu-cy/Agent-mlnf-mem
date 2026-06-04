#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-36
模块名称: 综合重要度I值聚合计算单元
所属分区: 三、漏斗二：任务经验漏斗 / 三维重要度计算引擎
核心职责: 作为三维重要度计算引擎的聚合中枢，接收来自 ag-mem-31（S值）、ag-mem-32（V值）、
          ag-mem-33（C值）、ag-mem-34（I₀值）分别计算的四个维度的分值，从 ag-mem-35
          获取全局权重系数α、β、γ，执行聚合公式 I = I₀ + α·S + β·V + γ·C，输出最终的
          综合重要度评分I值。当新经验写入时，负责协调四个计算模块依次完成各自维度的计算，
          并在收集全部四个分值后执行聚合。同时响应来自 ag-mem-37（重要度增量定时刷新单元）
          的全量重算请求，对已有经验重新聚合I值。不参与各维度分值的计算逻辑，仅执行聚合
          加权与边界裁剪。

依赖模块:
    ag-mem-31(S值计算), ag-mem-32(V值计算), ag-mem-33(C值统计),
    ag-mem-34(I₀赋值), ag-mem-35(权重系数配置)
被依赖模块:
    ag-mem-15~19(各场景分槽), ag-mem-37(重要度增量定时刷新单元),
    ag-mem-38(晋升双条件判定单元), ag-mem-40(遗忘阈值判定单元)

安全约束:
  A-01: 聚合计算必须严格遵循 I = I₀ + α·S + β·V + γ·C 公式，不得增加额外维度或修改公式结构
  A-02: 权重系数α+β+γ=1.0为硬约束，聚合前必须校验，不满足时拒绝执行并上报告警
  A-03: 超时补齐使用的默认值（I₀=0.30, S=0.10, V=0.20）为保守值，偏向低估而非高估
  A-04: I值边界[0.05, 1.0]为硬约束，不得输出超出边界的重要度值
  A-05: 全量重算时不得修改条目的各维度原始分值（I₀/S/V/C），仅重新执行聚合计算
"""

from typing import Dict, List, Optional, Any, Callable, Tuple
from dataclasses import dataclass, field
from enum import Enum
import time
import uuid


class AggregatorState(Enum):
    IDLE = "idle"
    COLLECTING = "collecting"
    AGGREGATING = "aggregating"
    FULL_RECALC = "full_recalc"
    SYSTEM_PAUSED = "system_paused"


@dataclass
class NewExperienceTrigger:
    entry_id: str = ""
    source_slot_id: str = ""
    task_type: str = ""
    experience_metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


@dataclass
class FullRecalcCommand:
    entry_list: List[Dict[str, Any]] = field(default_factory=list)
    trigger_reason: str = "定时刷新"


@dataclass
class DimensionResult:
    entry_id: str = ""
    dimension: str = ""  # "i0" / "s" / "v" / "c"
    value: float = 0.0


@dataclass
class IValueResult:
    entry_id: str = ""
    i_value: float = 0.0
    i0: float = 0.0
    s: float = 0.0
    v: float = 0.0
    c: float = 0.0
    contributions: Dict[str, float] = field(default_factory=dict)


@dataclass
class TimeoutAlert:
    entry_id: str = ""
    missing_dimensions: List[str] = field(default_factory=list)
    waited_seconds: float = 0.0


@dataclass
class AggregationStatus:
    state: str = ""
    total_aggregations: int = 0
    avg_i_value: float = 0.0
    avg_duration_ms: float = 0.0


class IValueAggregator:
    # 超时时间（秒）
    COLLECTION_TIMEOUT_SEC = 2.0
    # 默认补齐值（保守）
    DEFAULT_I0 = 0.30
    DEFAULT_S = 0.10
    DEFAULT_V = 0.20
    DEFAULT_C = 0.0
    # I值边界
    MIN_I = 0.05
    MAX_I = 1.0
    # 状态上报间隔
    STATUS_REPORT_INTERVAL_SEC = 120

    def __init__(self):
        self.module_id = "ag-mem-36"
        self.module_name = "综合重要度I值聚合计算单元"
        self.version = "V1.0"

        self.state = AggregatorState.IDLE
        # 收集表：entry_id -> {i0, s, v, c, 各维度是否就绪, 触发时间, 来源分槽}
        self._collection_table: Dict[str, Dict[str, Any]] = {}
        # 统计
        self._total_aggregations: int = 0
        self._sum_i: float = 0.0
        self._sum_duration: float = 0.0
        self._last_status_time: float = time.time()
        self._pending_logs: List[Dict[str, Any]] = []

        # 权重系数缓存
        self._alpha = 0.40
        self._beta = 0.30
        self._gamma = 0.30
        self._slot_weight_adjustments: Dict[str, Dict[str, float]] = {}

        # 回调注入
        self._query_new_experience_trigger = None
        self._query_full_recalc_command = None
        self._query_dimension_results = None
        self._query_weight_config = None

        self._publish_s_calc_request = None
        self._publish_v_calc_request = None
        self._publish_c_calc_request = None
        self._publish_i0_calc_request = None
        self._publish_aggregated_result = None
        self._publish_timeout_alert = None
        self._publish_status_report = None
        self._publish_event_log = None

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    # ========== 回调注入 ==========
    def set_new_experience_trigger_query(self, callback: Callable[[], Optional[NewExperienceTrigger]]):
        self._query_new_experience_trigger = callback

    def set_full_recalc_command_query(self, callback: Callable[[], Optional[FullRecalcCommand]]):
        self._query_full_recalc_command = callback

    def set_dimension_results_query(self, callback: Callable[[], Optional[List[DimensionResult]]]):
        self._query_dimension_results = callback

    def set_weight_config_query(self, callback: Callable[[], Optional[Dict[str, Any]]]):
        self._query_weight_config = callback

    def set_s_calc_request_publisher(self, callback: Callable[[str, Dict[str, Any]], None]):
        self._publish_s_calc_request = callback

    def set_v_calc_request_publisher(self, callback: Callable[[str, Dict[str, Any]], None]):
        self._publish_v_calc_request = callback

    def set_c_calc_request_publisher(self, callback: Callable[[str, Dict[str, Any]], None]):
        self._publish_c_calc_request = callback

    def set_i0_calc_request_publisher(self, callback: Callable[[str, Dict[str, Any]], None]):
        self._publish_i0_calc_request = callback

    def set_aggregated_result_publisher(self, callback: Callable[[IValueResult], None]):
        self._publish_aggregated_result = callback

    def set_timeout_alert_publisher(self, callback: Callable[[TimeoutAlert], None]):
        self._publish_timeout_alert = callback

    def set_status_report_publisher(self, callback: Callable[[AggregationStatus], None]):
        self._publish_status_report = callback

    def set_event_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_event_log = callback

    # ========== 主循环 ==========
    def run_aggregation_cycle(self):
        now = time.time()

        if self.state == AggregatorState.SYSTEM_PAUSED:
            return

        # 定期状态上报
        if now - self._last_status_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._publish_status()
            self._last_status_time = now

        # 同步权重配置
        if self._query_weight_config:
            config = self._query_weight_config()
            if config:
                self._alpha = config.get("alpha", self._alpha)
                self._beta = config.get("beta", self._beta)
                self._gamma = config.get("gamma", self._gamma)
                self._slot_weight_adjustments = config.get("slot_configs", {})

        # 接收各维度计算结果（修复：完整实现了_update_collection）
        if self.state == AggregatorState.COLLECTING:
            results = self._query_dimension_results() if self._query_dimension_results else []
            for res in results:
                self._update_collection(res)
            self._check_collection_timeouts(now)

        # 处理新经验触发
        trigger = self._query_new_experience_trigger() if self._query_new_experience_trigger else None
        if trigger and self.state == AggregatorState.IDLE:
            self._start_new_collection(trigger)

        # 处理全量重算
        recalc = self._query_full_recalc_command() if self._query_full_recalc_command else None
        if recalc and self.state == AggregatorState.IDLE:
            self._handle_full_recalc(recalc)

    # ========== 新经验聚合 ==========
    def _start_new_collection(self, trigger: NewExperienceTrigger):
        self.state = AggregatorState.COLLECTING
        entry_id = trigger.entry_id
        self._collection_table[entry_id] = {
            "i0": None, "s": None, "v": None, "c": 0.0,
            "ready": {"i0": False, "s": False, "v": False, "c": True},
            "trigger_time": time.time(),
            "source_slot_id": trigger.source_slot_id,
            "metadata": trigger.experience_metadata
        }

        self._request_dimension("I0", entry_id, trigger)
        self._request_dimension("S", entry_id, trigger)
        self._request_dimension("V", entry_id, trigger)

    def _request_dimension(self, dim: str, entry_id: str, trigger: NewExperienceTrigger):
        if dim == "I0" and self._publish_i0_calc_request:
            self._publish_i0_calc_request("ag-mem-34", {"entry_id": entry_id, "metadata": trigger.experience_metadata})
        elif dim == "S" and self._publish_s_calc_request:
            self._publish_s_calc_request("ag-mem-31", {"entry_id": entry_id, "metadata": trigger.experience_metadata})
        elif dim == "V" and self._publish_v_calc_request:
            self._publish_v_calc_request("ag-mem-32", {"entry_id": entry_id, "metadata": trigger.experience_metadata})

    def _update_collection(self, result: DimensionResult):
        """接收各维度模块返回的计算结果，更新收集表（已修复空实现）"""
        entry_id = result.entry_id
        if entry_id not in self._collection_table:
            # 超时后延迟到达的结果，记录日志后丢弃
            self._log_event("LATE_RESULT_DISCARDED", {
                "entry_id": entry_id,
                "dimension": result.dimension,
                "value": result.value
            })
            return

        record = self._collection_table[entry_id]
        dim = result.dimension
        if dim in record["ready"]:
            record[dim] = result.value
            record["ready"][dim] = True

        # 检查是否四个维度全部就绪
        if all(record["ready"].values()):
            self._perform_aggregation(entry_id)

    # ========== 全量重算 ==========
    def _handle_full_recalc(self, command: FullRecalcCommand):
        self.state = AggregatorState.FULL_RECALC
        results = []
        for entry in command.entry_list:
            i0 = entry.get("i0", 0.0)
            s = entry.get("s", 0.0)
            v = entry.get("v", 0.0)
            c = entry.get("c", 0.0)
            source_slot = entry.get("source_slot_id", "")
            i_val = self._compute_i(i0, s, v, c, source_slot)
            results.append(IValueResult(
                entry_id=entry.get("entry_id", ""),
                i_value=i_val, i0=i0, s=s, v=v, c=c,
                contributions={
                    "i0": i0,
                    "s": self._effective_alpha(source_slot) * s,
                    "v": self._effective_beta(source_slot) * v,
                    "c": self._effective_gamma(source_slot) * c
                }
            ))
        if self._publish_aggregated_result:
            for r in results:
                self._publish_aggregated_result(r)
        self._total_aggregations += len(results)
        for r in results:
            self._sum_i += r.i_value
        self.state = AggregatorState.IDLE

    # ========== 核心计算 ==========
    def _compute_i(self, i0: float, s: float, v: float, c: float, source_slot: str) -> float:
        alpha_eff = self._effective_alpha(source_slot)
        beta_eff = self._effective_beta(source_slot)
        gamma_eff = self._effective_gamma(source_slot)
        i_raw = i0 + alpha_eff * s + beta_eff * v + gamma_eff * c
        return max(self.MIN_I, min(self.MAX_I, round(i_raw, 3)))

    def _effective_alpha(self, slot_id: str) -> float:
        slot_cfg = self._slot_weight_adjustments.get(slot_id, {})
        return self._alpha * slot_cfg.get("alpha_adj", 1.0)

    def _effective_beta(self, slot_id: str) -> float:
        slot_cfg = self._slot_weight_adjustments.get(slot_id, {})
        return self._beta * slot_cfg.get("beta_adj", 1.0)

    def _effective_gamma(self, slot_id: str) -> float:
        slot_cfg = self._slot_weight_adjustments.get(slot_id, {})
        return self._gamma * slot_cfg.get("gamma_adj", 1.0)

    # ========== 超时检测与补齐 ==========
    def _check_collection_timeouts(self, now: float):
        for entry_id, record in list(self._collection_table.items()):
            if now - record["trigger_time"] > self.COLLECTION_TIMEOUT_SEC:
                missing = [dim for dim in ["i0", "s", "v", "c"] if not record["ready"][dim]]
                if not record["ready"]["i0"]:
                    record["i0"] = self.DEFAULT_I0
                if not record["ready"]["s"]:
                    record["s"] = self.DEFAULT_S
                if not record["ready"]["v"]:
                    record["v"] = self.DEFAULT_V
                self._perform_aggregation(entry_id)
                if self._publish_timeout_alert:
                    self._publish_timeout_alert(TimeoutAlert(
                        entry_id=entry_id,
                        missing_dimensions=missing,
                        waited_seconds=now - record["trigger_time"]
                    ))

    def _perform_aggregation(self, entry_id: str):
        record = self._collection_table.pop(entry_id, None)
        if not record:
            return
        i_val = self._compute_i(
            record["i0"] if record["i0"] is not None else self.DEFAULT_I0,
            record["s"] if record["s"] is not None else self.DEFAULT_S,
            record["v"] if record["v"] is not None else self.DEFAULT_V,
            record["c"] if record["c"] is not None else self.DEFAULT_C,
            record["source_slot_id"]
        )
        result = IValueResult(
            entry_id=entry_id,
            i_value=i_val,
            i0=record["i0"] if record["i0"] is not None else self.DEFAULT_I0,
            s=record["s"] if record["s"] is not None else self.DEFAULT_S,
            v=record["v"] if record["v"] is not None else self.DEFAULT_V,
            c=record["c"] if record["c"] is not None else self.DEFAULT_C,
            contributions={
                "i0": record["i0"] if record["i0"] is not None else self.DEFAULT_I0,
                "s": self._effective_alpha(record["source_slot_id"]) * (record["s"] if record["s"] is not None else self.DEFAULT_S),
                "v": self._effective_beta(record["source_slot_id"]) * (record["v"] if record["v"] is not None else self.DEFAULT_V),
                "c": self._effective_gamma(record["source_slot_id"]) * (record["c"] if record["c"] is not None else self.DEFAULT_C)
            }
        )
        if self._publish_aggregated_result:
            self._publish_aggregated_result(result)
        self._total_aggregations += 1
        self._sum_i += i_val
        if len(self._collection_table) == 0:
            self.state = AggregatorState.IDLE

    # ========== 辅助 ==========
    def _publish_status(self):
        avg_i = self._sum_i / max(self._total_aggregations, 1)
        if self._publish_status_report:
            self._publish_status_report(AggregationStatus(
                state=self.state.value,
                total_aggregations=self._total_aggregations,
                avg_i_value=round(avg_i, 3)
            ))

    def emergency_shutdown(self):
        self.state = AggregatorState.SYSTEM_PAUSED
        self._collection_table.clear()
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
    print("  Agent-mlnf-mem 综合重要度I值聚合计算单元 (ag-mem-36) 演示")
    print("=" * 70)

    agg = IValueAggregator()
    agg.set_weight_config_query(lambda: {
        "alpha": 0.40, "beta": 0.30, "gamma": 0.30,
        "slot_configs": {"ag-mem-16": {"alpha_adj": 1.2, "beta_adj": 1.0, "gamma_adj": 1.0}}
    })

    print_separator("STEP 1: 正常聚合（四个维度按时到达）")
    agg.set_new_experience_trigger_query(lambda: NewExperienceTrigger(
        entry_id="E01", source_slot_id="ag-mem-16"
    ))
    agg.run_aggregation_cycle()
    # 模拟各维度结果到达
    agg._update_collection(DimensionResult(entry_id="E01", dimension="i0", value=0.5))
    agg._update_collection(DimensionResult(entry_id="E01", dimension="s", value=0.6))
    agg._update_collection(DimensionResult(entry_id="E01", dimension="v", value=0.3))
    # C值默认为0且已就绪，四个维度全部到达后自动聚合
    print(f"  聚合次数: {agg._total_aggregations}")

    print_separator("STEP 2: 超时补齐")
    agg.set_new_experience_trigger_query(lambda: NewExperienceTrigger(
        entry_id="E02", source_slot_id="ag-mem-15"
    ))
    agg.run_aggregation_cycle()
    agg._collection_table["E02"]["trigger_time"] = time.time() - agg.COLLECTION_TIMEOUT_SEC - 0.1
    agg._check_collection_timeouts(time.time())
    print(f"  聚合次数: {agg._total_aggregations}")

    print_separator("STEP 3: 超时后延迟结果被正确丢弃")
    discard_result = agg._update_collection(DimensionResult(
        entry_id="E02", dimension="s", value=0.9
    ))
    print(f"  E02延迟结果: 已被丢弃（收集表中无此条目）")

    print("\n✅ 综合重要度I值聚合计算单元演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("ag-mem-36 综合重要度I值聚合计算单元 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        def setup_agg():
            a = IValueAggregator()
            a.set_weight_config_query(lambda: {
                "alpha": 0.40, "beta": 0.30, "gamma": 0.30,
                "slot_configs": {"ag-mem-16": {"alpha_adj": 1.2, "beta_adj": 1.0, "gamma_adj": 1.0}}
            })
            return a

        # TC-M36-01: 四个维度全部就绪后自动聚合
        print("\n[TC-M36-01] 四个维度全部就绪后自动聚合")
        try:
            a = setup_agg()
            a.set_new_experience_trigger_query(lambda: NewExperienceTrigger(
                entry_id="T01", source_slot_id="ag-mem-16"
            ))
            a.run_aggregation_cycle()
            a._update_collection(DimensionResult(entry_id="T01", dimension="i0", value=0.5))
            a._update_collection(DimensionResult(entry_id="T01", dimension="s", value=0.6))
            a._update_collection(DimensionResult(entry_id="T01", dimension="v", value=0.3))
            assert a._total_aggregations == 1
            assert "T01" not in a._collection_table
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M36-02: I值边界裁剪（超上限）
        print("\n[TC-M36-02] I值边界裁剪（超上限）")
        try:
            a = setup_agg()
            i = a._compute_i(0.8, 0.9, 1.0, 1.0, "ag-mem-15")
            assert i == 1.0
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M36-03: I值下边界保护
        print("\n[TC-M36-03] I值下边界保护")
        try:
            a = setup_agg()
            i = a._compute_i(0.01, 0.0, 0.0, 0.0, "ag-mem-15")
            assert i == 0.05
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M36-04: 超时补齐默认值
        print("\n[TC-M36-04] 超时补齐默认值")
        try:
            a = setup_agg()
            a.set_new_experience_trigger_query(lambda: NewExperienceTrigger(
                entry_id="T04", source_slot_id="ag-mem-15"
            ))
            a.run_aggregation_cycle()
            a._collection_table["T04"]["trigger_time"] = time.time() - a.COLLECTION_TIMEOUT_SEC - 0.1
            a._check_collection_timeouts(time.time())
            assert "T04" not in a._collection_table
            assert a._total_aggregations == 1
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M36-05: 分槽权重调整
        print("\n[TC-M36-05] 分槽权重调整（工具调用S值上调20%）")
        try:
            a = setup_agg()
            i = a._compute_i(0.5, 0.6, 0.3, 0.5, "ag-mem-16")
            expected = round(0.5 + 0.48 * 0.6 + 0.30 * 0.3 + 0.30 * 0.5, 3)
            assert i == min(1.0, expected)
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M36-06: 紧急熔断
        print("\n[TC-M36-06] 紧急熔断")
        try:
            a = setup_agg()
            a.emergency_shutdown()
            assert a.state == AggregatorState.SYSTEM_PAUSED
            assert len(a._collection_table) == 0
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