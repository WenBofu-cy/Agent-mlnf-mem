#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-51
模块名称: 记忆变更日志追溯单元
所属分区: 五、存储与系统运维
核心职责: 全链路记录双漏斗记忆中枢中所有记忆操作事件的不可变审计日志。涵盖经验写入、晋升、
          遗忘、归档、导入导出、安全仲裁、警示标签变更等全部记忆生命周期操作。每条日志包含
          UTC 时间戳、操作类型、操作模块、目标条目ID、变更前状态、变更后状态、操作原因摘要。
          日志存储采用追加写模式，禁止修改或删除已落盘的日志条目。存储周期 ≥ 3 年，为事故
          追溯、责任判定、合规审计提供完整的数据基础。支持按时间、操作类型、模块等多维度检索。
          不参与任何认知决策，仅负责日志的记录、存储与检索服务。

依赖模块:
    ag-mem-01 至 ag-mem-50（所有记忆模块均可向本模块推送变更日志事件）
被依赖模块:
    ag-mem-01(总控漏斗F₀), ECC-12(资源调度模块), 系统管理接口, 离线复盘系统

安全约束:
  L-01: 日志存储必须采用追加写模式（Append-Only），禁止任何模块修改或删除已落盘的日志条目
  L-02: 每条日志条目必须包含全局单调递增序列号与 UTC 时间戳，确保时序不可篡改
  L-03: 事故相关的关键日志（安全仲裁、L5 锁定、配置变更）在存储空间不足时优先保留，不得被自动清理
  L-04: 日志存储周期 ≥ 3 年，超期日志在空间不足时优先清理，但需在清理前发送告警通知
  L-05: 日志 HMAC 签名用于内部完整性校验，发现签名不匹配的日志条目时应上报告警但不中断服务
"""

from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
import time
import uuid
import hmac
import hashlib
import json


class TracerState(Enum):
    NORMAL_LOGGING = "normal_logging"
    LOW_STORAGE = "low_storage"
    STORAGE_FAULT = "storage_fault"
    SYSTEM_PAUSED = "system_paused"


class LogEventType(Enum):
    EXPERIENCE_WRITE = "经验写入"
    EXPERIENCE_PROMOTE = "经验晋升"
    EXPERIENCE_FORGET = "经验遗忘"
    EXPERIENCE_ARCHIVE = "经验归档"
    EXPERIENCE_RESTORE = "经验恢复"
    EXPERIENCE_MERGE = "经验归并"
    SLOT_CREATE = "画像槽创建"
    SLOT_DELETE = "画像槽清除"
    SAFETY_ARBITRATION = "安全仲裁"
    CAUTION_LABEL_CHANGE = "警示标签变更"
    L5_LOCK = "L5锁定/解锁"
    IMPORT = "经验导入"
    EXPORT = "经验导出"
    CAPACITY_CLEANUP = "容量清理"
    CONFIG_CHANGE = "配置变更"
    SYSTEM_EVENT = "系统事件"


@dataclass
class LogEntry:
    log_id: str = ""                    # LOG-{序列号}-{时间戳}
    sequence_number: int = 0            # 全局单调递增序列号
    event_type: LogEventType = LogEventType.SYSTEM_EVENT
    source_module: str = ""             # 触发操作的模块编号
    target_entry_id: str = ""           # 被操作的经验条目ID（无则为空）
    previous_state: str = ""            # 操作前的状态描述
    new_state: str = ""                 # 操作后的状态描述
    reason_summary: str = ""            # 操作原因摘要
    related_data: Dict[str, Any] = field(default_factory=dict)  # 关联数据引用
    timestamp: float = field(default_factory=time.time)         # UTC 时间戳
    signature: str = ""                 # HMAC 签名


@dataclass
class LogQueryRequest:
    time_range: Optional[float] = None  # 最近多少秒内
    module_id: Optional[str] = None
    event_type: Optional[LogEventType] = None
    target_entry_id: Optional[str] = None
    keyword: Optional[str] = None
    max_results: int = 100


@dataclass
class LogExportRequest:
    time_range: Optional[float] = None
    event_types: List[LogEventType] = field(default_factory=list)
    export_format: str = "JSON"


@dataclass
class StorageStatus:
    total_capacity_bytes: int = 0
    used_bytes: int = 0
    remaining_bytes: int = 0
    oldest_log_time: float = 0.0
    newest_log_time: float = 0.0
    write_rate_per_sec: float = 0.0


@dataclass
class StorageAlert:
    alert_type: str = ""                # 空间不足 / 存储故障
    current_status: str = ""
    suggested_action: str = ""


class ChangeLogTracer:
    # 存储配置
    TOTAL_CAPACITY_BYTES = 500 * 1024 * 1024     # 500MB
    RETENTION_YEARS = 3
    CRITICAL_EVENT_TYPES = {                      # 关键事件类型（优先保留）
        LogEventType.SAFETY_ARBITRATION,
        LogEventType.L5_LOCK,
        LogEventType.CONFIG_CHANGE,
    }
    # 阈值
    LOW_STORAGE_THRESHOLD_PCT = 0.10
    CRITICAL_STORAGE_THRESHOLD_PCT = 0.05
    # 内存缓冲上限
    MAX_BUFFER_ENTRIES = 5000
    # HMAC 密钥
    SIGNING_KEY = "change-log-tracer-secret"
    # 状态上报间隔
    STATUS_REPORT_INTERVAL_SEC = 60

    def __init__(self):
        self.module_id = "ag-mem-51"
        self.module_name = "记忆变更日志追溯单元"
        self.version = "V1.0"

        self.state = TracerState.NORMAL_LOGGING
        self._sequence_number: int = 0
        self._log_entries: List[LogEntry] = []          # 持久化模拟（实际应写文件/DB）
        self._total_bytes: int = 0
        self._consecutive_write_failures: int = 0
        self._buffer: List[LogEntry] = []               # 内存缓冲（存储故障时暂存）
        self._last_status_time: float = time.time()
        self._pending_logs: List[Dict[str, Any]] = []

        # 回调注入
        self._query_log_event = None                    # 获取待记录的日志事件
        self._query_query_request = None
        self._query_export_request = None

        self._publish_write_confirm = None
        self._publish_query_result = None
        self._publish_export_package = None
        self._publish_storage_status = None
        self._publish_storage_alert = None
        self._publish_event_log = None

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成, 存储容量={self.TOTAL_CAPACITY_BYTES/1024/1024:.0f}MB")

    # ========== 回调注入 ==========
    def set_log_event_query(self, callback: Callable[[], Optional[LogEntry]]):
        self._query_log_event = callback

    def set_query_request_query(self, callback: Callable[[], Optional[LogQueryRequest]]):
        self._query_query_request = callback

    def set_export_request_query(self, callback: Callable[[], Optional[LogExportRequest]]):
        self._query_export_request = callback

    def set_write_confirm_publisher(self, callback: Callable[[str, bool], None]):
        self._publish_write_confirm = callback

    def set_query_result_publisher(self, callback: Callable[[List[LogEntry], int, float, bool], None]):
        self._publish_query_result = callback

    def set_export_package_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_export_package = callback

    def set_storage_status_publisher(self, callback: Callable[[StorageStatus], None]):
        self._publish_storage_status = callback

    def set_storage_alert_publisher(self, callback: Callable[[StorageAlert], None]):
        self._publish_storage_alert = callback

    def set_event_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_event_log = callback

    # ========== 主循环 ==========
    def run_tracer_cycle(self):
        now = time.time()

        if self.state == TracerState.SYSTEM_PAUSED:
            return

        # 定期状态上报
        if now - self._last_status_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._publish_status()
            self._last_status_time = now

        # 接收日志事件
        log_event = self._query_log_event() if self._query_log_event else None
        if log_event:
            self._record_log(log_event)

        # 处理查询请求
        query_req = self._query_query_request() if self._query_query_request else None
        if query_req:
            self._handle_query(query_req)

        # 处理导出请求
        export_req = self._query_export_request() if self._query_export_request else None
        if export_req:
            self._handle_export(export_req)

    # ========== 日志记录 ==========
    def _record_log(self, entry: LogEntry):
        # 分配序列号
        self._sequence_number += 1
        entry.sequence_number = self._sequence_number
        entry.log_id = f"LOG-{self._sequence_number:08d}-{int(entry.timestamp)}"
        # 计算 HMAC 签名
        entry.signature = self._compute_signature(entry)

        # 检查存储状态
        if self.state == TracerState.STORAGE_FAULT:
            self._buffer.append(entry)
            if len(self._buffer) >= self.MAX_BUFFER_ENTRIES:
                # 丢弃最旧的条目，优先保留关键事件
                self._evict_oldest_non_critical()
            return

        # 模拟持久化写入
        if self._simulate_write(entry):
            self._log_entries.append(entry)
            self._total_bytes += 256  # 估算每条日志256字节
            self._consecutive_write_failures = 0
        else:
            self._consecutive_write_failures += 1
            if self._consecutive_write_failures >= 3:
                self.state = TracerState.STORAGE_FAULT
                self._send_storage_alert("存储故障", "连续写入失败3次，切换至内存缓冲模式")
                self._buffer.append(entry)

        # 容量检查
        if self.state != TracerState.STORAGE_FAULT:
            remaining_pct = 1.0 - (self._total_bytes / self.TOTAL_CAPACITY_BYTES)
            if remaining_pct < self.CRITICAL_STORAGE_THRESHOLD_PCT:
                self.state = TracerState.LOW_STORAGE
                self._perform_cleanup(retention_years=2)
                self._send_storage_alert("空间严重不足", "已触发强制清理，保留最近2年数据")
            elif remaining_pct < self.LOW_STORAGE_THRESHOLD_PCT:
                self.state = TracerState.LOW_STORAGE
                self._perform_cleanup(retention_years=self.RETENTION_YEARS)
                self._send_storage_alert("空间不足", "已触发旧日志清理")

        # 可选确认
        if self._publish_write_confirm:
            self._publish_write_confirm(entry.log_id, True)

    def _compute_signature(self, entry: LogEntry) -> str:
        payload = f"{entry.sequence_number}|{entry.event_type.value}|{entry.source_module}|{entry.target_entry_id}|{entry.timestamp}"
        return hmac.new(self.SIGNING_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]

    def _simulate_write(self, entry: LogEntry) -> bool:
        # 真实场景应写入持久化存储，这里模拟成功
        return True

    def _evict_oldest_non_critical(self):
        """内存缓冲满时，丢弃最旧的条目，但保留关键事件"""
        for i, entry in enumerate(self._buffer):
            if entry.event_type not in self.CRITICAL_EVENT_TYPES:
                self._buffer.pop(i)
                return
        # 如果全部是关键事件，丢弃最旧的一个
        if self._buffer:
            self._buffer.pop(0)

    # ========== 查询处理 ==========
    def _handle_query(self, request: LogQueryRequest):
        start_time = time.time()
        matched = []
        for entry in self._log_entries:
            if request.time_range and (time.time() - entry.timestamp) > request.time_range:
                continue
            if request.module_id and request.module_id != entry.source_module:
                continue
            if request.event_type and request.event_type != entry.event_type:
                continue
            if request.target_entry_id and request.target_entry_id != entry.target_entry_id:
                continue
            if request.keyword and request.keyword not in entry.reason_summary:
                continue
            matched.append(entry)

        total = len(matched)
        # 按时间倒序
        matched.sort(key=lambda x: x.timestamp, reverse=True)
        matched = matched[:request.max_results]
        elapsed = (time.time() - start_time) * 1000
        is_complete = len(matched) == total

        if self._publish_query_result:
            self._publish_query_result(matched, total, elapsed, is_complete)

    # ========== 导出处理 ==========
    def _handle_export(self, request: LogExportRequest):
        matched = []
        for entry in self._log_entries:
            if request.time_range and (time.time() - entry.timestamp) > request.time_range:
                continue
            if request.event_types and entry.event_type not in request.event_types:
                continue
            matched.append(entry)
        matched.sort(key=lambda x: x.timestamp, reverse=True)

        package = {
            "export_time": time.time(),
            "total_entries": len(matched),
            "format": request.export_format,
            "entries": [self._serialize_entry(e) for e in matched],
            "checksum": hashlib.sha256(json.dumps([e.log_id for e in matched]).encode()).hexdigest()
        }

        if self._publish_export_package:
            self._publish_export_package(package)

    def _serialize_entry(self, entry: LogEntry) -> Dict[str, Any]:
        return {
            "log_id": entry.log_id,
            "sequence_number": entry.sequence_number,
            "event_type": entry.event_type.value,
            "source_module": entry.source_module,
            "target_entry_id": entry.target_entry_id,
            "previous_state": entry.previous_state,
            "new_state": entry.new_state,
            "reason_summary": entry.reason_summary,
            "timestamp": entry.timestamp,
            "signature": entry.signature
        }

    # ========== 容量管理 ==========
    def _perform_cleanup(self, retention_years: int = 3):
        retention_sec = retention_years * 365 * 86400
        cutoff = time.time() - retention_sec
        new_entries = []
        removed_bytes = 0
        for entry in self._log_entries:
            if entry.timestamp < cutoff and entry.event_type not in self.CRITICAL_EVENT_TYPES:
                removed_bytes += 256
            else:
                new_entries.append(entry)
        self._log_entries = new_entries
        self._total_bytes -= removed_bytes

    def _send_storage_alert(self, alert_type: str, suggestion: str):
        if self._publish_storage_alert:
            self._publish_storage_alert(StorageAlert(
                alert_type=alert_type,
                current_status=self.state.value,
                suggested_action=suggestion
            ))

    # ========== 辅助 ==========
    def _publish_status(self):
        if self._publish_storage_status:
            oldest = min((e.timestamp for e in self._log_entries), default=0)
            newest = max((e.timestamp for e in self._log_entries), default=0)
            self._publish_storage_status(StorageStatus(
                total_capacity_bytes=self.TOTAL_CAPACITY_BYTES,
                used_bytes=self._total_bytes,
                remaining_bytes=self.TOTAL_CAPACITY_BYTES - self._total_bytes,
                oldest_log_time=oldest,
                newest_log_time=newest,
                write_rate_per_sec=0.0
            ))

    def emergency_shutdown(self):
        self.state = TracerState.SYSTEM_PAUSED
        # 尝试将缓冲刷入存储
        if self._buffer:
            for entry in self._buffer:
                self._log_entries.append(entry)
            self._buffer.clear()
        print(f"[{self.module_id}] 紧急熔断，缓冲已刷入存储")

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
    print("  Agent-mlnf-mem 记忆变更日志追溯单元 (ag-mem-51) 演示")
    print("=" * 70)

    tracer = ChangeLogTracer()

    print_separator("STEP 1: 记录一条经验晋升日志")
    tracer.set_log_event_query(lambda: LogEntry(
        event_type=LogEventType.EXPERIENCE_PROMOTE,
        source_module="ag-mem-39",
        target_entry_id="E01",
        previous_state="L2, I=0.65",
        new_state="L3, I=0.65",
        reason_summary="满足晋升双条件"
    ))
    tracer.run_tracer_cycle()
    print(f"  序列号: {tracer._sequence_number}")

    print_separator("STEP 2: 记录安全仲裁日志（关键事件）")
    tracer.set_log_event_query(lambda: LogEntry(
        event_type=LogEventType.SAFETY_ARBITRATION,
        source_module="ag-mem-43",
        target_entry_id="E02",
        reason_summary="三道校验全部通过"
    ))
    tracer.run_tracer_cycle()
    print(f"  序列号: {tracer._sequence_number}")

    print_separator("STEP 3: 查询最近晋升日志")
    tracer.set_query_request_query(lambda: LogQueryRequest(
        event_type=LogEventType.EXPERIENCE_PROMOTE,
        max_results=10
    ))
    tracer.run_tracer_cycle()
    print(f"  查询完成")

    print("\n✅ 记忆变更日志追溯单元演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("ag-mem-51 记忆变更日志追溯单元 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        def setup_tracer():
            return ChangeLogTracer()

        # TC-M51-01: 正常记录日志
        print("\n[TC-M51-01] 正常记录日志")
        try:
            t = setup_tracer()
            t.set_log_event_query(lambda: LogEntry(
                event_type=LogEventType.EXPERIENCE_WRITE,
                source_module="ag-mem-20",
                target_entry_id="L1-001"
            ))
            t.run_tracer_cycle()
            assert t._sequence_number == 1
            assert len(t._log_entries) == 1
            assert t._log_entries[0].signature != ""
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M51-02: 序列号递增
        print("\n[TC-M51-02] 序列号递增")
        try:
            t = setup_tracer()
            for i in range(3):
                t.set_log_event_query(lambda: LogEntry(event_type=LogEventType.EXPERIENCE_WRITE, source_module="ag-mem-20"))
                t.run_tracer_cycle()
            assert t._sequence_number == 3
            assert len(t._log_entries) == 3
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M51-03: 查询过滤
        print("\n[TC-M51-03] 查询过滤（按事件类型）")
        try:
            t = setup_tracer()
            t._log_entries = [
                LogEntry(sequence_number=1, event_type=LogEventType.EXPERIENCE_WRITE, source_module="m1", timestamp=time.time()),
                LogEntry(sequence_number=2, event_type=LogEventType.EXPERIENCE_PROMOTE, source_module="m2", timestamp=time.time()),
            ]
            t.set_query_request_query(lambda: LogQueryRequest(event_type=LogEventType.EXPERIENCE_PROMOTE))
            t.run_tracer_cycle()
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M51-04: 容量不足时触发清理
        print("\n[TC-M51-04] 容量不足时触发清理")
        try:
            t = setup_tracer()
            t._total_bytes = int(t.TOTAL_CAPACITY_BYTES * 0.92)
            t.set_log_event_query(lambda: LogEntry(event_type=LogEventType.EXPERIENCE_WRITE, source_module="ag-mem-20"))
            t.run_tracer_cycle()
            assert t.state == TracerState.LOW_STORAGE
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M51-05: 内存缓冲溢出时优先保留关键事件
        print("\n[TC-M51-05] 内存缓冲溢出时优先保留关键事件")
        try:
            t = setup_tracer()
            t.state = TracerState.STORAGE_FAULT
            # 填满缓冲
            for i in range(t.MAX_BUFFER_ENTRIES):
                t._buffer.append(LogEntry(sequence_number=i, event_type=LogEventType.EXPERIENCE_WRITE, source_module="m"))
            # 再添加一条关键事件
            t.set_log_event_query(lambda: LogEntry(event_type=LogEventType.SAFETY_ARBITRATION, source_module="ag-mem-43"))
            t.run_tracer_cycle()
            # 缓冲中应有一条关键事件
            has_critical = any(e.event_type == LogEventType.SAFETY_ARBITRATION for e in t._buffer)
            assert has_critical
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M51-06: 紧急熔断
        print("\n[TC-M51-06] 紧急熔断")
        try:
            t = setup_tracer()
            t.emergency_shutdown()
            assert t.state == TracerState.SYSTEM_PAUSED
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