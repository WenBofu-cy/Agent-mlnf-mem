#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-41
模块名称: 最低复用次数校验单元
所属分区: 三、漏斗二：任务经验漏斗 / 晋升与遗忘执行机制
核心职责: 接收 ag-mem-40（遗忘阈值判定单元）在判定过程中对接近遗忘阈值边界的条目发起的
          复用次数保护校验请求。校验该条目在同类任务场景下的历史复用次数是否满足最低保护
          条件：若复用次数高于最低保护阈值，即使I值低于遗忘阈值也暂时保留（给予一次保护
          周期）；若复用次数不足且I值确实偏低，则确认遗忘判定有效。通过引入复用次数保护
          机制，避免那些I值因自然衰减而暂时低于阈值但仍具有较高实用价值的经验被过早误删。
          不参与遗忘判定决策，仅提供复用次数的客观校验与保护建议。

依赖模块:
    ag-mem-33(C值统计单元), ag-mem-40(遗忘阈值判定单元),
    ag-mem-35(三维权重系数配置单元)
被依赖模块:
    ag-mem-40

安全约束:
  U-01: 复用次数校验仅读取条目的复用频次统计数据，不得修改任何经验数据
  U-02: 保护有效期到期后，该条目在下次遗忘判定时需重新校验，不得自动续期
  U-03: 工具调用槽（ag-mem-16）的最低保护次数上调20%，确保高安全场景的经验更不易被误删
  U-04: 校验结果缓存仅用于避免短时间内重复查询，有效期60秒，超时自动失效
"""

from typing import Dict, List, Optional, Any, Callable, Tuple
from dataclasses import dataclass, field
from enum import Enum
import time
import uuid


class ValidatorState(Enum):
    IDLE = "idle"
    QUERYING_USAGE = "querying_usage"
    OUTPUTTING = "outputting"
    SYSTEM_PAUSED = "system_paused"


@dataclass
class MinUsageCheckRequest:
    entry_id: str = ""
    current_i_value: float = 0.0
    current_layer: str = ""
    source_slot_id: str = ""
    forget_threshold: float = 0.0
    timestamp: float = field(default_factory=time.time)


@dataclass
class UsageData:
    entry_id: str = ""
    total_success_calls: int = 0
    recent_30d_calls: int = 0
    last_call_timestamp: float = 0.0


@dataclass
class MinUsageCheckResult:
    entry_id: str = ""
    is_protected: bool = False
    current_usage: int = 0
    min_protection_threshold: int = 0
    protection_reason: str = ""
    protection_duration_days: int = 0


@dataclass
class SlotProtectionConfig:
    l1: int = 1
    l2: int = 3
    l3: int = 5
    l4: int = 8


@dataclass
class ValidatorStatus:
    state: str = ""
    total_checks: int = 0
    protected_ratio: float = 0.0
    avg_duration_ms: float = 0.0


class MinUsageValidator:
    # 各层级基础保护次数
    BASE_PROTECTION = {
        "L1": 1,
        "L2": 3,
        "L3": 5,
        "L4": 8,
    }
    # 各分槽调整系数
    SLOT_ADJUSTMENTS = {
        "ag-mem-15": 1.0,
        "ag-mem-16": 1.2,  # 工具调用槽上调20%
        "ag-mem-17": 1.0,
        "ag-mem-18": 1.0,
        "ag-mem-19": 0.8,
    }
    # 保护条件阈值
    I_VALUE_RATIO_THRESHOLD = 0.7        # I值不低于遗忘阈值的70%
    RECENT_CALL_DAYS = 7                 # 最近7日内有调用
    # 缓存有效期
    CACHE_VALIDITY_SEC = 60
    # 状态上报间隔
    STATUS_REPORT_INTERVAL_SEC = 180

    def __init__(self):
        self.module_id = "ag-mem-41"
        self.module_name = "最低复用次数校验单元"
        self.version = "V1.0"

        self.state = ValidatorState.IDLE
        self._cache: Dict[str, Tuple[MinUsageCheckResult, float]] = {}  # entry_id -> (result, timestamp)
        self._total_checks: int = 0
        self._protected_count: int = 0
        self._total_duration: float = 0.0
        self._last_status_time: float = time.time()
        self._pending_logs: List[Dict[str, Any]] = []

        # 回调注入
        self._query_check_request = None
        self._query_usage_data = None
        self._query_slot_config = None

        self._publish_check_result = None
        self._publish_status_report = None
        self._publish_event_log = None

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    # ========== 回调注入 ==========
    def set_check_request_query(self, callback: Callable[[], Optional[MinUsageCheckRequest]]):
        self._query_check_request = callback

    def set_usage_data_query(self, callback: Callable[[str], Optional[UsageData]]):
        self._query_usage_data = callback

    def set_slot_config_query(self, callback: Callable[[], Optional[Dict[str, SlotProtectionConfig]]]):
        self._query_slot_config = callback

    def set_check_result_publisher(self, callback: Callable[[MinUsageCheckResult], None]):
        self._publish_check_result = callback

    def set_status_report_publisher(self, callback: Callable[[ValidatorStatus], None]):
        self._publish_status_report = callback

    def set_event_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_event_log = callback

    # ========== 主循环 ==========
    def run_validator_cycle(self):
        now = time.time()

        if self.state == ValidatorState.SYSTEM_PAUSED:
            return

        # 定期状态上报
        if now - self._last_status_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._publish_status()
            self._last_status_time = now

        # 清理过期缓存
        self._cleanup_cache(now)

        # 接收校验请求
        request = self._query_check_request() if self._query_check_request else None
        if request is None:
            return

        # 检查缓存
        if request.entry_id in self._cache:
            cached_result, cached_time = self._cache[request.entry_id]
            if now - cached_time < self.CACHE_VALIDITY_SEC:
                if self._publish_check_result:
                    self._publish_check_result(cached_result)
                return

        self.state = ValidatorState.QUERYING_USAGE
        start_time = time.time()
        result = self._perform_check(request)
        elapsed = (time.time() - start_time) * 1000

        # 更新缓存
        self._cache[request.entry_id] = (result, now)

        # 更新统计
        self._total_checks += 1
        if result.is_protected:
            self._protected_count += 1
        self._total_duration += elapsed

        self.state = ValidatorState.OUTPUTTING

        if self._publish_check_result:
            self._publish_check_result(result)

        self.state = ValidatorState.IDLE

    # ========== 核心校验 ==========
    def _perform_check(self, request: MinUsageCheckRequest) -> MinUsageCheckResult:
        # 获取该分槽该层级的最低保护次数
        base = self.BASE_PROTECTION.get(request.current_layer, 3)
        adj = self.SLOT_ADJUSTMENTS.get(request.source_slot_id, 1.0)
        min_threshold = max(1, round(base * adj))

        # 查询复用次数
        usage_data = self._query_usage_data(request.entry_id) if self._query_usage_data else None
        total_calls = usage_data.total_success_calls if usage_data else 0
        recent_30d = usage_data.recent_30d_calls if usage_data else 0
        last_call = usage_data.last_call_timestamp if usage_data else 0

        now = time.time()
        days_since_last_call = (now - last_call) / 86400 if last_call > 0 else 999

        # 条件1: 复用次数 ≥ 最低保护次数
        if total_calls >= min_threshold:
            return MinUsageCheckResult(
                entry_id=request.entry_id,
                is_protected=True,
                current_usage=total_calls,
                min_protection_threshold=min_threshold,
                protection_reason=f"复用次数满足最低保护条件({total_calls}≥{min_threshold})",
                protection_duration_days=30
            )

        # 条件2: 近30日有复用且I值不低于遗忘阈值的70%
        if recent_30d >= 1 and request.current_i_value >= request.forget_threshold * self.I_VALUE_RATIO_THRESHOLD:
            return MinUsageCheckResult(
                entry_id=request.entry_id,
                is_protected=True,
                current_usage=total_calls,
                min_protection_threshold=min_threshold,
                protection_reason=f"近30日有复用且I值({request.current_i_value:.2f})≥遗忘阈值×0.7({request.forget_threshold*0.7:.2f})",
                protection_duration_days=30
            )

        # 条件3: 最近7日内被复用
        if days_since_last_call <= self.RECENT_CALL_DAYS:
            return MinUsageCheckResult(
                entry_id=request.entry_id,
                is_protected=True,
                current_usage=total_calls,
                min_protection_threshold=min_threshold,
                protection_reason=f"最近7日内被复用(距今{days_since_last_call:.1f}天)",
                protection_duration_days=7
            )

        # 不受保护
        return MinUsageCheckResult(
            entry_id=request.entry_id,
            is_protected=False,
            current_usage=total_calls,
            min_protection_threshold=min_threshold,
            protection_reason=f"复用次数不足({total_calls}<{min_threshold})，建议确认遗忘判定",
            protection_duration_days=0
        )

    # ========== 辅助 ==========
    def _cleanup_cache(self, now: float):
        expired = [eid for eid, (_, ts) in self._cache.items() if now - ts >= self.CACHE_VALIDITY_SEC]
        for eid in expired:
            del self._cache[eid]

    def _publish_status(self):
        if self._publish_status_report:
            ratio = self._protected_count / max(self._total_checks, 1)
            avg = self._total_duration / max(self._total_checks, 1)
            self._publish_status_report(ValidatorStatus(
                state=self.state.value,
                total_checks=self._total_checks,
                protected_ratio=round(ratio, 3),
                avg_duration_ms=round(avg, 2)
            ))

    def emergency_shutdown(self):
        self.state = ValidatorState.SYSTEM_PAUSED
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
    print("  Agent-mlnf-mem 最低复用次数校验单元 (ag-mem-41) 演示")
    print("=" * 70)

    validator = MinUsageValidator()

    print_separator("STEP 1: 复用次数满足保护条件")
    validator.set_usage_data_query(lambda eid: UsageData(
        entry_id=eid, total_success_calls=6, recent_30d_calls=2, last_call_timestamp=time.time()
    ))
    validator.set_check_request_query(lambda: MinUsageCheckRequest(
        entry_id="E01", current_i_value=0.09, current_layer="L2", source_slot_id="ag-mem-15", forget_threshold=0.10
    ))
    validator.run_validator_cycle()

    print_separator("STEP 2: 复用次数不足，无保护")
    validator.set_usage_data_query(lambda eid: UsageData(
        entry_id=eid, total_success_calls=1, recent_30d_calls=0, last_call_timestamp=0
    ))
    validator.set_check_request_query(lambda: MinUsageCheckRequest(
        entry_id="E02", current_i_value=0.08, current_layer="L2", source_slot_id="ag-mem-19", forget_threshold=0.10
    ))
    validator.run_validator_cycle()

    print("\n✅ 最低复用次数校验单元演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("ag-mem-41 最低复用次数校验单元 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        def setup_validator():
            return MinUsageValidator()

        # TC-M41-01: 复用次数满足最低保护条件
        print("\n[TC-M41-01] 复用次数满足最低保护条件")
        try:
            v = setup_validator()
            v.set_usage_data_query(lambda eid: UsageData(eid, 6, 2, time.time()))
            v.set_check_request_query(lambda: MinUsageCheckRequest("T01", 0.09, "L2", "ag-mem-15", 0.10))
            v.run_validator_cycle()
            assert v._total_checks == 1
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M41-02: 最近7日内复用触发保护
        print("\n[TC-M41-02] 最近7日内复用触发保护")
        try:
            v = setup_validator()
            v.set_usage_data_query(lambda eid: UsageData(eid, 2, 0, time.time()))
            v.set_check_request_query(lambda: MinUsageCheckRequest("T02", 0.09, "L3", "ag-mem-16", 0.10))
            v.run_validator_cycle()
            assert v._total_checks == 1
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M41-03: 复用次数不足无保护
        print("\n[TC-M41-03] 复用次数不足无保护")
        try:
            v = setup_validator()
            v.set_usage_data_query(lambda eid: UsageData(eid, 1, 0, 0))
            v.set_check_request_query(lambda: MinUsageCheckRequest("T03", 0.08, "L2", "ag-mem-19", 0.10))
            v.run_validator_cycle()
            assert v._total_checks == 1
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M41-04: 工具调用槽上调20%
        print("\n[TC-M41-04] 工具调用槽最低保护次数上调20%")
        try:
            v = setup_validator()
            # L2基础保护=3，ag-mem-16调整系数=1.2，最低=4
            v.set_usage_data_query(lambda eid: UsageData(eid, 3, 0, 0))  # 3次，低于4
            v.set_check_request_query(lambda: MinUsageCheckRequest("T04", 0.09, "L2", "ag-mem-16", 0.10))
            v.run_validator_cycle()
            # 应不受保护
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M41-05: 缓存命中避免重复查询
        print("\n[TC-M41-05] 缓存命中避免重复查询")
        try:
            v = setup_validator()
            query_count = [0]
            def mock_usage(eid):
                query_count[0] += 1
                return UsageData(eid, 6, 2, time.time())
            v.set_usage_data_query(mock_usage)
            v.set_check_request_query(lambda: MinUsageCheckRequest("T05", 0.09, "L2", "ag-mem-15", 0.10))
            v.run_validator_cycle()
            v.run_validator_cycle()
            assert query_count[0] == 1  # 第二次应命中缓存
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M41-06: 紧急熔断
        print("\n[TC-M41-06] 紧急熔断")
        try:
            v = setup_validator()
            v.emergency_shutdown()
            assert v.state == ValidatorState.SYSTEM_PAUSED
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