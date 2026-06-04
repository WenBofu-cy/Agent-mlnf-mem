#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-48
模块名称: 全局容量配额管控单元
所属分区: 五、存储与系统运维
核心职责: 监控双漏斗记忆中枢（漏斗一 + 漏斗二）的全局存储使用量，实施分层级的容量配额
          管控。当存储逼近预置上限（默认 100MB，可在配置文件中自定义扩展至设备存储上限）
          时，触发分级响应：总量达 80% 时发出温和预警，90% 时启动低重要度条目清理（优先
          清理 L1/L2 可覆写条目），95% 时暂停非关键写入并强制清理。所有清理操作均通过
          ag-mem-42（冗余记忆删除与归档单元）安全执行。同时向各层级存储单元及 ag-mem-01
          （总控漏斗F₀）周期性上报容量使用状态。不参与经验内容的删除决策，仅负责容量监控
          与清理触发。

依赖模块:
    ag-mem-20~28(各层级存储单元，上报存储使用量), ag-mem-42(冗余记忆删除与归档单元)
被依赖模块:
    ag-mem-01(总控漏斗F₀), ag-mem-03(漏斗二专属调度单元),
    ag-mem-05(画像槽创建单元), ag-mem-20~28(各层级存储单元)

安全约束:
  Q-01: 容量上限可在配置文件中自定义扩展至设备存储上限，但运行时不得低于最小安全容量
  Q-02: 清理操作必须通过 ag-mem-42 执行，本模块不得直接操作任何存储层的数据删除
  Q-03: L5 核心层条目永远不受容量管控驱动的清理操作影响
  Q-04: 容量紧急时暂停写入的范围仅限于 L1/L2 层级，L3-L5 的晋升写入不受影响
"""

from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
import time
import uuid


class QuotaState(Enum):
    NORMAL = "normal"
    CAPACITY_WARNING = "capacity_warning"
    CAPACITY_CRITICAL = "capacity_critical"
    SYSTEM_PAUSED = "system_paused"


@dataclass
class LayerStorageReport:
    source_layer: str = ""
    entry_count: int = 0
    usage_bytes: int = 0
    max_quota_bytes: int = 0
    usage_pct: float = 0.0
    timestamp: float = field(default_factory=time.time)


@dataclass
class QuotaQueryRequest:
    requester_module: str = ""
    query_layer: str = ""               # 空表示全局
    query_type: str = "usage_pct"       # usage_pct / available_space / quota_limit


@dataclass
class QuotaQueryResult:
    query_layer: str = ""
    current_usage_pct: float = 0.0
    available_space_bytes: int = 0
    quota_limit_bytes: int = 0


@dataclass
class CapacityAlert:
    alert_level: str = ""
    current_usage_pct: float = 0.0
    trigger_threshold: float = 0.0
    suggested_action: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class CleanupCommand:
    target_layers: List[str] = field(default_factory=list)
    strategy: str = ""                  # 温和 / 强制
    target_release_bytes: int = 0
    priority: str = "温和"


@dataclass
class CleanupCompleteReceipt:
    cleaned_count: int = 0
    released_bytes: int = 0
    remaining_usage_pct: float = 0.0


@dataclass
class WriteGateNotice:
    paused: bool = False
    affected_layers: List[str] = field(default_factory=list)
    reason: str = ""
    estimated_recovery_time_sec: float = 0.0


@dataclass
class QuotaStatusReport:
    state: QuotaState = QuotaState.NORMAL
    total_usage_pct: float = 0.0
    layer_usage_pct: Dict[str, float] = field(default_factory=dict)
    available_bytes: int = 0


class GlobalQuotaController:
    # 容量配置
    DEFAULT_TOTAL_CAPACITY_BYTES = 100 * 1024 * 1024     # 100MB
    MIN_SAFE_CAPACITY_BYTES = 10 * 1024 * 1024           # 10MB 最小安全容量
    WARN_THRESHOLD = 0.80
    CRITICAL_THRESHOLD = 0.90
    TARGET_WARN_RELEASE_PCT = 0.75
    TARGET_CRITICAL_RELEASE_PCT = 0.85
    MAX_CLEANUP_DURATION_SEC = 10
    STATUS_REPORT_INTERVAL_SEC = 30

    def __init__(self):
        self.module_id = "ag-mem-48"
        self.module_name = "全局容量配额管控单元"
        self.version = "V1.0"

        self.state = QuotaState.NORMAL
        self._total_capacity_bytes = self.DEFAULT_TOTAL_CAPACITY_BYTES
        self._layer_usage: Dict[str, LayerStorageReport] = {}
        self._total_usage_bytes: int = 0
        self._write_paused: bool = False
        self._last_status_time: float = time.time()
        self._last_cleanup_result: Optional[CleanupCompleteReceipt] = None
        self._pending_logs: List[Dict[str, Any]] = []

        # 回调注入
        self._query_layer_report = None
        self._query_quota_request = None
        self._query_cleanup_complete = None

        self._publish_quota_status = None
        self._publish_capacity_alert = None
        self._publish_cleanup_command = None
        self._publish_write_gate_notice = None
        self._publish_quota_query_result = None
        self._publish_event_log = None

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成, 总容量={self._total_capacity_bytes/1024/1024:.0f}MB")

    # ========== 回调注入 ==========
    def set_layer_report_query(self, callback: Callable[[], Optional[List[LayerStorageReport]]]):
        self._query_layer_report = callback

    def set_quota_request_query(self, callback: Callable[[], Optional[QuotaQueryRequest]]):
        self._query_quota_request = callback

    def set_cleanup_complete_query(self, callback: Callable[[], Optional[CleanupCompleteReceipt]]):
        self._query_cleanup_complete = callback

    def set_quota_status_publisher(self, callback: Callable[[QuotaStatusReport], None]):
        self._publish_quota_status = callback

    def set_capacity_alert_publisher(self, callback: Callable[[CapacityAlert], None]):
        self._publish_capacity_alert = callback

    def set_cleanup_command_publisher(self, callback: Callable[[CleanupCommand], None]):
        self._publish_cleanup_command = callback

    def set_write_gate_notice_publisher(self, callback: Callable[[WriteGateNotice], None]):
        self._publish_write_gate_notice = callback

    def set_quota_query_result_publisher(self, callback: Callable[[QuotaQueryResult], None]):
        self._publish_quota_query_result = callback

    def set_event_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_event_log = callback

    # ========== 主循环 ==========
    def run_controller_cycle(self):
        now = time.time()

        if self.state == QuotaState.SYSTEM_PAUSED:
            return

        # 接收各层级存储使用量上报
        reports = self._query_layer_report() if self._query_layer_report else []
        if reports:
            self._update_usage(reports)

        # 处理清理完成回执
        if self.state in (QuotaState.CAPACITY_WARNING, QuotaState.CAPACITY_CRITICAL):
            cleanup_result = self._query_cleanup_complete() if self._query_cleanup_complete else None
            if cleanup_result:
                self._handle_cleanup_complete(cleanup_result)

        # 定期状态上报
        if now - self._last_status_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._publish_status()
            self._last_status_time = now

        # 处理容量查询请求
        query_req = self._query_quota_request() if self._query_quota_request else None
        if query_req:
            self._handle_quota_query(query_req)

    # ========== 用量更新与状态检查 ==========
    def _update_usage(self, reports: List[LayerStorageReport]):
        for report in reports:
            self._layer_usage[report.source_layer] = report
        self._total_usage_bytes = sum(r.usage_bytes for r in self._layer_usage.values())
        total_pct = self._calculate_total_usage_pct()

        # 状态跃迁检查
        if total_pct >= self.CRITICAL_THRESHOLD and self.state != QuotaState.CAPACITY_CRITICAL:
            self.state = QuotaState.CAPACITY_CRITICAL
            self._trigger_critical_response(total_pct)
        elif total_pct >= self.WARN_THRESHOLD and self.state == QuotaState.NORMAL:
            self.state = QuotaState.CAPACITY_WARNING
            self._trigger_warning_response(total_pct)

    def _trigger_warning_response(self, usage_pct: float):
        self._send_alert("警告", usage_pct)
        # 温和清理 L1/L2 低重要度条目
        target_release = max(0, self._total_usage_bytes - int(self._total_capacity_bytes * self.TARGET_WARN_RELEASE_PCT))
        if target_release > 0:
            self._send_cleanup_command(["L1", "L2"], "温和", target_release, "温和")

    def _trigger_critical_response(self, usage_pct: float):
        self._send_alert("紧急", usage_pct)
        # 暂停非关键写入
        if not self._write_paused:
            self._write_paused = True
            self._send_write_gate_notice(True, ["L1", "L2"], "容量紧急，暂停非关键写入")

        # 强制清理 L1/L2/L3 低重要度条目
        target_release = max(0, self._total_usage_bytes - int(self._total_capacity_bytes * self.TARGET_CRITICAL_RELEASE_PCT))
        if target_release > 0:
            self._send_cleanup_command(["L1", "L2", "L3"], "强制", target_release, "紧急")

    def _handle_cleanup_complete(self, result: CleanupCompleteReceipt):
        self._last_cleanup_result = result
        # 重新计算使用率
        self._total_usage_bytes = max(0, self._total_usage_bytes - result.released_bytes)
        total_pct = self._calculate_total_usage_pct()

        # 检查是否可以降级
        if self.state == QuotaState.CAPACITY_CRITICAL and total_pct < self.WARN_THRESHOLD:
            self.state = QuotaState.CAPACITY_WARNING
            self._restore_writes()
        if self.state == QuotaState.CAPACITY_WARNING and total_pct < self.WARN_THRESHOLD - 0.05:
            self.state = QuotaState.NORMAL

    def _restore_writes(self):
        if self._write_paused:
            self._write_paused = False
            self._send_write_gate_notice(False, ["L1", "L2"], "容量恢复正常，恢复写入")

    # ========== 查询处理 ==========
    def _handle_quota_query(self, request: QuotaQueryRequest):
        if request.query_layer and request.query_layer in self._layer_usage:
            layer = self._layer_usage[request.query_layer]
            result = QuotaQueryResult(
                query_layer=request.query_layer,
                current_usage_pct=layer.usage_pct,
                available_space_bytes=layer.max_quota_bytes - layer.usage_bytes,
                quota_limit_bytes=layer.max_quota_bytes
            )
        else:
            result = QuotaQueryResult(
                query_layer="全局",
                current_usage_pct=self._calculate_total_usage_pct(),
                available_space_bytes=self._total_capacity_bytes - self._total_usage_bytes,
                quota_limit_bytes=self._total_capacity_bytes
            )
        if self._publish_quota_query_result:
            self._publish_quota_query_result(result)

    # ========== 辅助方法 ==========
    def _calculate_total_usage_pct(self) -> float:
        if self._total_capacity_bytes <= 0:
            return 0.0
        return round(min(self._total_usage_bytes / self._total_capacity_bytes, 1.0), 3)

    def _send_alert(self, level: str, usage_pct: float):
        threshold = self.CRITICAL_THRESHOLD if level == "紧急" else self.WARN_THRESHOLD
        if self._publish_capacity_alert:
            self._publish_capacity_alert(CapacityAlert(
                alert_level=level,
                current_usage_pct=usage_pct,
                trigger_threshold=threshold,
                suggested_action="触发清理" if level == "警告" else "暂停写入并强制清理"
            ))

    def _send_cleanup_command(self, layers: List[str], strategy: str, target_bytes: int, priority: str):
        if self._publish_cleanup_command:
            self._publish_cleanup_command(CleanupCommand(
                target_layers=layers,
                strategy=strategy,
                target_release_bytes=target_bytes,
                priority=priority
            ))

    def _send_write_gate_notice(self, paused: bool, layers: List[str], reason: str):
        if self._publish_write_gate_notice:
            self._publish_write_gate_notice(WriteGateNotice(
                paused=paused,
                affected_layers=layers,
                reason=reason
            ))

    def _publish_status(self):
        if self._publish_quota_status:
            layer_pcts = {layer: report.usage_pct for layer, report in self._layer_usage.items()}
            self._publish_quota_status(QuotaStatusReport(
                state=self.state,
                total_usage_pct=self._calculate_total_usage_pct(),
                layer_usage_pct=layer_pcts,
                available_bytes=self._total_capacity_bytes - self._total_usage_bytes
            ))

    def set_total_capacity(self, capacity_bytes: int):
        if capacity_bytes < self.MIN_SAFE_CAPACITY_BYTES:
            self._log_event("CAPACITY_CHANGE_REJECTED", {"reason": "低于最小安全容量"})
            return
        self._total_capacity_bytes = capacity_bytes
        self._log_event("CAPACITY_CHANGED", {"new_capacity_bytes": capacity_bytes})

    def get_state(self) -> QuotaState:
        return self.state

    def emergency_shutdown(self):
        self.state = QuotaState.SYSTEM_PAUSED
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
    print("  Agent-mlnf-mem 全局容量配额管控单元 (ag-mem-48) 演示")
    print("=" * 70)

    controller = GlobalQuotaController()

    print_separator("STEP 1: 正常状态（使用率50%）")
    controller.set_layer_report_query(lambda: [
        LayerStorageReport(source_layer="L1", usage_bytes=30*1024*1024, max_quota_bytes=60*1024*1024, usage_pct=0.5),
        LayerStorageReport(source_layer="L2", usage_bytes=10*1024*1024, max_quota_bytes=25*1024*1024, usage_pct=0.4),
    ])
    controller.run_controller_cycle()
    print(f"  状态: {controller.state.value}")

    print_separator("STEP 2: 容量预警（使用率82%）触发温和清理")
    controller.set_layer_report_query(lambda: [
        LayerStorageReport(source_layer="L1", usage_bytes=55*1024*1024, max_quota_bytes=60*1024*1024, usage_pct=0.9),
        LayerStorageReport(source_layer="L2", usage_bytes=22*1024*1024, max_quota_bytes=25*1024*1024, usage_pct=0.88),
    ])
    controller.run_controller_cycle()
    print(f"  状态: {controller.state.value}")

    print("\n✅ 全局容量配额管控单元演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("ag-mem-48 全局容量配额管控单元 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        def setup_controller():
            return GlobalQuotaController()

        # TC-M48-01: 正常状态无告警
        print("\n[TC-M48-01] 正常状态无告警")
        try:
            c = setup_controller()
            c.set_layer_report_query(lambda: [
                LayerStorageReport(source_layer="L1", usage_bytes=40*1024*1024, max_quota_bytes=60*1024*1024, usage_pct=0.67),
            ])
            c.run_controller_cycle()
            assert c.state == QuotaState.NORMAL
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M48-02: 容量预警触发温和清理
        print("\n[TC-M48-02] 容量预警触发温和清理")
        try:
            c = setup_controller()
            c.set_layer_report_query(lambda: [
                LayerStorageReport(source_layer="L1", usage_bytes=55*1024*1024, max_quota_bytes=60*1024*1024, usage_pct=0.9),
                LayerStorageReport(source_layer="L2", usage_bytes=22*1024*1024, max_quota_bytes=25*1024*1024, usage_pct=0.88),
            ])
            c.run_controller_cycle()
            assert c.state == QuotaState.CAPACITY_WARNING
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M48-03: 容量紧急触发强制清理并暂停写入
        print("\n[TC-M48-03] 容量紧急触发强制清理并暂停写入")
        try:
            c = setup_controller()
            c.set_layer_report_query(lambda: [
                LayerStorageReport(source_layer="L1", usage_bytes=58*1024*1024, max_quota_bytes=60*1024*1024, usage_pct=0.97),
                LayerStorageReport(source_layer="L2", usage_bytes=24*1024*1024, max_quota_bytes=25*1024*1024, usage_pct=0.96),
            ])
            c.run_controller_cycle()
            assert c.state == QuotaState.CAPACITY_CRITICAL
            assert c._write_paused
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M48-04: 清理完成后降级
        print("\n[TC-M48-04] 清理完成后状态降级")
        try:
            c = setup_controller()
            c.state = QuotaState.CAPACITY_CRITICAL
            c._write_paused = True
            c._total_usage_bytes = 95 * 1024 * 1024
            c.set_cleanup_complete_query(lambda: CleanupCompleteReceipt(cleaned_count=10, released_bytes=20*1024*1024, remaining_usage_pct=0.75))
            c.run_controller_cycle()
            assert c.state != QuotaState.CAPACITY_CRITICAL
            assert not c._write_paused
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M48-05: 容量查询
        print("\n[TC-M48-05] 容量查询")
        try:
            c = setup_controller()
            c.set_quota_request_query(lambda: QuotaQueryRequest(requester_module="ag-mem-01"))
            c.run_controller_cycle()
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M48-06: 紧急熔断
        print("\n[TC-M48-06] 紧急熔断")
        try:
            c = setup_controller()
            c.emergency_shutdown()
            assert c.state == QuotaState.SYSTEM_PAUSED
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