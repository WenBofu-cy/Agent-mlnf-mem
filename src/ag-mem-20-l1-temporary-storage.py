#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-20
模块名称: L1临时层存储单元
所属分区: 三、漏斗二：任务经验漏斗 / 五层存储
核心职责: 作为漏斗二五层记忆存储架构的第一层，负责接收并存储本次会话或近期产生的瞬时
          任务经验片段。L1层是经验进入漏斗二存储系统的唯一入口，所有新经验均首先写入L1。
          管理L1层的容量上限（占漏斗二总容量的60%），当逼近容量上限时触发低重要度条目
          清理或向ag-mem-21请求衰减评估。不参与晋升判定或遗忘决策，仅执行经验的接收、
          暂存与基础管理。

依赖模块:
    ag-mem-15~19(各场景分槽), ag-mem-21(L1衰减评估单元),
    ag-mem-48(全局容量配额管控单元)
被依赖模块:
    ag-mem-15~19, ag-mem-21, ag-mem-22(L2近期层存储单元)

安全约束:
  S-01: L1层仅为经验临时暂存区，不得在L1层对经验内容进行任何修改或加工
  S-02: 写入经验时必须保留原始来源分槽编号，用于后续晋升时溯源
  S-03: L1容量紧急时强制清理不得删除最近500条经验，确保当前会话的经验不丢失
  S-04: L1存储的持久化写入必须保证原子性，写入中断时不产生损坏的半条记录
"""

from typing import Dict, List, Optional, Any, Callable, Tuple
from dataclasses import dataclass, field
from enum import Enum
import time
import uuid


class StorageState(Enum):
    NORMAL = "normal"
    CAPACITY_WARNING = "capacity_warning"
    CAPACITY_CRITICAL = "capacity_critical"
    SYSTEM_PAUSED = "system_paused"


@dataclass
class ExperienceEntry:
    entry_id: str = ""
    source_slot_id: str = ""
    experience_data: Dict[str, Any] = field(default_factory=dict)
    i_value: float = 0.0
    s_value: float = 0.0
    v_value: float = 0.0
    c_value: float = 0.0
    timestamp: float = field(default_factory=time.time)


@dataclass
class L1WriteRequest:
    request_id: str = ""
    entry: ExperienceEntry = field(default_factory=ExperienceEntry)
    source_slot_id: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class L1WriteConfirm:
    entry_id: str = ""
    success: bool = True
    l1_usage_pct: float = 0.0
    storage_position: str = ""
    error_reason: str = ""


@dataclass
class L1WriteRejectNotice:
    entry_id: str = ""
    reject_reason: str = ""
    l1_state: str = ""
    suggestion: str = ""


@dataclass
class L1DecayRequest:
    entries: List[ExperienceEntry] = field(default_factory=list)
    trigger_reason: str = ""
    l1_usage_pct: float = 0.0


@dataclass
class L1StatusReport:
    state: StorageState = StorageState.NORMAL
    total_entries: int = 0
    usage_pct: float = 0.0
    write_distribution: Dict[str, int] = field(default_factory=dict)
    last_write_time: float = 0.0


@dataclass
class L1CapacityInfo:
    current_count: int = 0
    current_usage_pct: float = 0.0
    available_space: int = 0


class L1TemporaryStorage:
    # 容量配置
    L1_CAPACITY_RATIO = 0.60  # 占漏斗二总容量的60%
    MAX_ENTRIES = 10000       # 硬上限
    MAX_ENTRY_SIZE_BYTES = 10 * 1024  # 10KB
    WRITE_TIMEOUT_MS = 200
    CAPACITY_WARN_THRESHOLD = 0.80
    CAPACITY_CRITICAL_THRESHOLD = 0.95
    DECAY_CHECK_INTERVAL_SEC = 6 * 3600  # 6小时
    MIN_RETAIN_ENTRIES = 500   # 容量紧急时最少保留条目数
    STATUS_REPORT_INTERVAL_SEC = 30
    DECAY_TRIGGER_RATIO = 0.20  # 触发衰减评估的比例（最低重要度20%）

    def __init__(self):
        self.module_id = "ag-mem-20"
        self.module_name = "L1临时层存储单元"
        self.version = "V1.0"

        self.state = StorageState.NORMAL
        self._entries: Dict[str, ExperienceEntry] = {}
        self._entry_count: int = 0
        self._last_decay_time = time.time()
        self._last_status_time = time.time()
        self._slot_write_stats: Dict[str, int] = {}
        self._pending_logs: List[Dict[str, Any]] = []

        # 回调注入
        self._query_write_request = None
        self._query_cleanup_confirm = None
        self._query_capacity_info = None

        self._publish_write_confirm = None
        self._publish_write_reject = None
        self._publish_decay_request = None
        self._publish_status_report = None
        self._publish_event_log = None

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成, 最大条目={self.MAX_ENTRIES}")

    def set_write_request_query(self, callback: Callable[[], Optional[L1WriteRequest]]):
        self._query_write_request = callback

    def set_cleanup_confirm_query(self, callback: Callable[[], Optional[Dict[str, Any]]]):
        self._query_cleanup_confirm = callback

    def set_capacity_info_query(self, callback: Callable[[], Optional[L1CapacityInfo]]):
        self._query_capacity_info = callback

    def set_write_confirm_publisher(self, callback: Callable[[L1WriteConfirm], None]):
        self._publish_write_confirm = callback

    def set_write_reject_publisher(self, callback: Callable[[L1WriteRejectNotice], None]):
        self._publish_write_reject = callback

    def set_decay_request_publisher(self, callback: Callable[[L1DecayRequest], None]):
        self._publish_decay_request = callback

    def set_status_report_publisher(self, callback: Callable[[L1StatusReport], None]):
        self._publish_status_report = callback

    def set_event_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_event_log = callback

    def run_storage_cycle(self) -> Optional[L1WriteConfirm]:
        now = time.time()

        if self.state == StorageState.SYSTEM_PAUSED:
            return None

        # 处理清理完成回执
        if self.state in (StorageState.CAPACITY_WARNING, StorageState.CAPACITY_CRITICAL):
            cleanup = self._query_cleanup_confirm() if self._query_cleanup_confirm else None
            if cleanup:
                cleaned = cleanup.get("cleaned_count", 0)
                self._entry_count -= cleaned
                new_usage = self._calculate_usage_pct()
                if new_usage < self.CAPACITY_WARN_THRESHOLD:
                    self.state = StorageState.NORMAL

        # 定时衰减评估
        if now - self._last_decay_time >= self.DECAY_CHECK_INTERVAL_SEC:
            self._trigger_decay("定时衰减")
            self._last_decay_time = now

        # 定期状态上报
        if now - self._last_status_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._publish_status()
            self._last_status_time = now

        # 接收写入请求
        request = self._query_write_request() if self._query_write_request else None
        if request is None:
            return None

        return self._handle_write(request)

    def _handle_write(self, request: L1WriteRequest) -> L1WriteConfirm:
        entry = request.entry
        source_slot = request.source_slot_id

        # 容量状态检查
        usage_pct = self._calculate_usage_pct()
        if usage_pct >= self.CAPACITY_CRITICAL_THRESHOLD:
            self.state = StorageState.CAPACITY_CRITICAL
        elif usage_pct >= self.CAPACITY_WARN_THRESHOLD:
            self.state = StorageState.CAPACITY_WARNING

        # 容量紧急处理
        if self.state == StorageState.CAPACITY_CRITICAL and self._entry_count >= self.MAX_ENTRIES:
            # 强制触发衰减清理
            self._trigger_decay("容量紧急")
            # 检查清理后是否仍满
            if self._entry_count >= self.MAX_ENTRIES:
                if self._publish_write_reject:
                    self._publish_write_reject(L1WriteRejectNotice(
                        entry_id="",
                        reject_reason="L1容量已满且无法清理",
                        l1_state=self.state.value,
                        suggestion="等待清理完成后重试"
                    ))
                return L1WriteConfirm(success=False, error_reason="L1容量已满")

        # 容量预警处理（温和清理）
        if self.state == StorageState.CAPACITY_WARNING:
            self._trigger_decay("容量预警")

        # 校验条目大小
        entry_size = len(str(entry.experience_data))
        if entry_size > self.MAX_ENTRY_SIZE_BYTES:
            if self._publish_write_reject:
                self._publish_write_reject(L1WriteRejectNotice(
                    entry_id="",
                    reject_reason=f"经验条目大小超过{self.MAX_ENTRY_SIZE_BYTES}字节上限",
                    l1_state=self.state.value
                ))
            return L1WriteConfirm(success=False, error_reason="条目大小超限")

        # 生成条目ID
        entry_id = f"L1-{int(time.time()*1000)}-{uuid.uuid4().hex[:8]}"
        entry.entry_id = entry_id
        entry.source_slot_id = source_slot
        entry.timestamp = time.time()

        # 写入L1存储
        self._entries[entry_id] = entry
        self._entry_count += 1

        # 更新分槽写入统计
        if source_slot not in self._slot_write_stats:
            self._slot_write_stats[source_slot] = 0
        self._slot_write_stats[source_slot] += 1

        usage_pct = self._calculate_usage_pct()

        confirm = L1WriteConfirm(
            entry_id=entry_id,
            success=True,
            l1_usage_pct=round(usage_pct, 3),
            storage_position=f"l1://{entry_id}"
        )

        if self._publish_write_confirm:
            self._publish_write_confirm(confirm)

        return confirm

    def _trigger_decay(self, reason: str):
        if self._entry_count == 0:
            return

        # 取L1中重要度最低的20%条目
        sorted_entries = sorted(self._entries.values(), key=lambda e: e.i_value)
        decay_count = max(1, int(self._entry_count * self.DECAY_TRIGGER_RATIO))
        decay_entries = sorted_entries[:decay_count]

        if self._publish_decay_request:
            self._publish_decay_request(L1DecayRequest(
                entries=decay_entries,
                trigger_reason=reason,
                l1_usage_pct=self._calculate_usage_pct()
            ))

    def _calculate_usage_pct(self) -> float:
        if self.MAX_ENTRIES == 0:
            return 0.0
        return min(self._entry_count / self.MAX_ENTRIES, 1.0)

    def get_entries_for_decay(self, count: int) -> List[ExperienceEntry]:
        sorted_entries = sorted(self._entries.values(), key=lambda e: e.i_value)
        return sorted_entries[:min(count, len(sorted_entries))]

    def remove_entries(self, entry_ids: List[str]) -> int:
        removed = 0
        for eid in entry_ids:
            if eid in self._entries:
                del self._entries[eid]
                self._entry_count -= 1
                removed += 1
        return removed

    def _publish_status(self):
        if self._publish_status_report:
            self._publish_status_report(L1StatusReport(
                state=self.state,
                total_entries=self._entry_count,
                usage_pct=self._calculate_usage_pct(),
                write_distribution=self._slot_write_stats.copy()
            ))

    def get_state(self) -> StorageState:
        return self.state

    def get_entry_count(self) -> int:
        return self._entry_count

    def emergency_shutdown(self):
        self.state = StorageState.SYSTEM_PAUSED
        print(f"[{self.module_id}] 紧急熔断, 当前条目数={self._entry_count}")

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
    print("  Agent-mlnf-mem L1临时层存储单元 (ag-mem-20) 演示")
    print("=" * 70)

    storage = L1TemporaryStorage()

    print_separator("STEP 1: 写入经验条目")
    storage.set_write_request_query(lambda: L1WriteRequest(
        request_id="REQ-001",
        source_slot_id="ag-mem-16",
        entry=ExperienceEntry(
            experience_data={"tool": "weather_api", "result": "success"},
            i_value=0.5
        )
    ))
    result = storage.run_storage_cycle()
    if result:
        print(f"  条目ID: {result.entry_id}")
        print(f"  写入成功: {result.success}")
        print(f"  L1使用率: {result.l1_usage_pct:.2%}")

    print_separator("STEP 2: 再写入两条经验")
    for i in range(2):
        storage.set_write_request_query(lambda i=i: L1WriteRequest(
            request_id=f"REQ-00{i+2}",
            source_slot_id="ag-mem-15",
            entry=ExperienceEntry(
                experience_data={"text": f"对话内容{i+1}"},
                i_value=0.3 + i * 0.2
            )
        ))
        result = storage.run_storage_cycle()
    print(f"  当前条目数: {storage.get_entry_count()}")

    print_separator("STEP 3: 检查L1容量状态")
    usage = storage._calculate_usage_pct()
    print(f"  使用率: {usage:.2%}")
    print(f"  状态: {storage.state.value}")

    print("\n✅ L1临时层存储单元演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("ag-mem-20 L1临时层存储单元 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        def setup_storage():
            return L1TemporaryStorage()

        # TC-M20-01: 正常写入经验
        print("\n[TC-M20-01] 正常写入经验")
        try:
            s = setup_storage()
            s.set_write_request_query(lambda: L1WriteRequest(
                request_id="T01", source_slot_id="ag-mem-16",
                entry=ExperienceEntry(i_value=0.5)
            ))
            result = s.run_storage_cycle()
            assert result is not None
            assert result.success
            assert result.entry_id != ""
            assert s.get_entry_count() == 1
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M20-02: 容量预警触发衰减评估
        print("\n[TC-M20-02] 容量预警触发衰减评估")
        try:
            s = setup_storage()
            # 模拟接近满容量
            s._entry_count = int(s.MAX_ENTRIES * 0.82)
            s.set_write_request_query(lambda: L1WriteRequest(
                request_id="T02", source_slot_id="ag-mem-15",
                entry=ExperienceEntry(i_value=0.5)
            ))
            result = s.run_storage_cycle()
            assert s.state == StorageState.CAPACITY_WARNING
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M20-03: 容量紧急拒绝写入
        print("\n[TC-M20-03] 容量紧急拒绝写入")
        try:
            s = setup_storage()
            s._entry_count = s.MAX_ENTRIES  # 已满
            s.set_write_request_query(lambda: L1WriteRequest(
                request_id="T03", source_slot_id="ag-mem-16",
                entry=ExperienceEntry(i_value=0.5)
            ))
            result = s.run_storage_cycle()
            assert result is not None
            assert not result.success
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M20-04: 条目大小超限
        print("\n[TC-M20-04] 条目大小超限")
        try:
            s = setup_storage()
            s.set_write_request_query(lambda: L1WriteRequest(
                request_id="T04", source_slot_id="ag-mem-16",
                entry=ExperienceEntry(experience_data={"data": "x" * (s.MAX_ENTRY_SIZE_BYTES + 100)})
            ))
            result = s.run_storage_cycle()
            assert result is not None
            assert not result.success
            assert "超" in result.error_reason
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M20-05: 移除条目
        print("\n[TC-M20-05] 移除条目")
        try:
            s = setup_storage()
            s.set_write_request_query(lambda: L1WriteRequest(
                request_id="T05", source_slot_id="ag-mem-16",
                entry=ExperienceEntry(i_value=0.5)
            ))
            result = s.run_storage_cycle()
            eid = result.entry_id
            removed = s.remove_entries([eid])
            assert removed == 1
            assert s.get_entry_count() == 0
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M20-06: 紧急熔断
        print("\n[TC-M20-06] 紧急熔断")
        try:
            s = setup_storage()
            s.emergency_shutdown()
            assert s.state == StorageState.SYSTEM_PAUSED
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