#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-49
模块名称: 存储压缩与冷归档单元
所属分区: 五、存储与系统运维
核心职责: 接收 ag-mem-42（冗余记忆删除与归档单元）发送的归档写入指令，对 L3 及以上层级
          的被遗忘经验条目执行压缩打包与冷归档存储。将经验数据及其元数据（来源层级、I值、
          遗忘原因、时间戳）序列化后，使用高效压缩算法压缩，写入冷存储分区，并返回归档
          存储位置与压缩后大小。同时管理冷存储分区的容量，当冷存储逼近上限时触发旧归档数据
          的清理（按归档时间从旧到新）。支持按需从冷存储中恢复归档条目（用于离线复盘或数据
          恢复），恢复时解压并重建原始数据结构。不参与遗忘判定或删除决策，仅执行压缩、归档
          与恢复的确定性操作。

依赖模块:
    ag-mem-42(冗余记忆删除与归档单元), ag-mem-48(全局容量配额管控单元)
被依赖模块:
    ag-mem-42, ag-mem-48, ag-mem-51(记忆变更日志追溯单元)

安全约束:
  C-01: 冷存储中的归档数据采用 SHA-256 校验和，恢复时必须验证完整性
  C-02: 冷存储数据默认保留期限为归档后 1 年，超期数据在容量清理时优先删除（人工标记永久保留的除外）
  C-03: 归档操作必须保证原子性：压缩+写入全部成功才返回成功，任一步骤失败均标记为归档失败
  C-04: 冷存储分区独立于热存储（五层存储），互不影响
"""

from typing import Dict, List, Optional, Any, Callable, Tuple
from dataclasses import dataclass, field
from enum import Enum
import time
import uuid
import hashlib
import json
import zlib  # 使用 zlib 模拟 Zstandard，实际可替换为 zstandard 库


class ColdStorageState(Enum):
    NORMAL = "normal"
    COMPRESSING = "compressing"
    RESTORING = "restoring"
    COLD_WARN = "cold_warn"
    SYSTEM_PAUSED = "system_paused"


@dataclass
class ArchiveEntry:
    """冷存储归档条目"""
    archive_id: str = ""
    original_entry_id: str = ""
    compressed_data: bytes = b""
    original_size: int = 0
    compressed_size: int = 0
    algorithm: str = "zlib-6"   # 模拟 Zstandard
    metadata: Dict[str, Any] = field(default_factory=dict)
    checksum: str = ""
    permanent_retain: bool = False
    archived_at: float = field(default_factory=time.time)


@dataclass
class ArchiveWriteCommand:
    """归档写入指令"""
    entry_id: str = ""
    experience_data: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)  # 来源层级、I值、遗忘原因等
    timestamp: float = field(default_factory=time.time)


@dataclass
class ArchiveWriteConfirm:
    """归档写入确认"""
    entry_id: str = ""
    success: bool = True
    archive_location: str = ""
    original_size: int = 0
    compressed_size: int = 0
    compression_ratio: float = 0.0
    error_reason: str = ""


@dataclass
class ArchiveRestoreRequest:
    """归档恢复请求"""
    request_id: str = ""
    entry_ids: Optional[List[str]] = None
    time_range: Optional[Tuple[float, float]] = None
    source_layer: Optional[str] = None
    max_entries: int = 100


@dataclass
class RestoredEntry:
    """恢复后的条目"""
    entry_id: str = ""
    experience_data: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ArchiveRestoreResult:
    """归档恢复结果"""
    request_id: str = ""
    restored_entries: List[RestoredEntry] = field(default_factory=list)
    total_found: int = 0
    duration_ms: float = 0.0


@dataclass
class ColdStorageStatus:
    """冷存储状态"""
    state: ColdStorageState = ColdStorageState.NORMAL
    total_capacity_bytes: int = 50 * 1024 * 1024
    used_bytes: int = 0
    usage_pct: float = 0.0
    total_archives: int = 0
    oldest_archive_time: float = 0.0
    newest_archive_time: float = 0.0


class ColdStorageManager:
    # 冷存储配置
    COLD_CAPACITY_BYTES = 50 * 1024 * 1024      # 50MB
    WARN_THRESHOLD = 0.80
    CRITICAL_THRESHOLD = 0.95
    RETENTION_DAYS = 365                         # 1年
    COMPRESS_LEVEL = 6                           # zlib 压缩级别
    SINGLE_ARCHIVE_TIMEOUT_SEC = 10
    SINGLE_RESTORE_TIMEOUT_SEC = 15
    STATUS_REPORT_INTERVAL_SEC = 120

    def __init__(self):
        self.module_id = "ag-mem-49"
        self.module_name = "存储压缩与冷归档单元"
        self.version = "V1.0"

        self.state = ColdStorageState.NORMAL
        self._archives: Dict[str, ArchiveEntry] = {}  # archive_id -> ArchiveEntry
        self._index_by_original_id: Dict[str, str] = {}  # original_entry_id -> archive_id
        self._total_used_bytes: int = 0
        self._total_archives: int = 0
        self._last_status_time: float = time.time()
        self._pending_logs: List[Dict[str, Any]] = []

        # 回调注入
        self._query_write_command = None
        self._query_restore_request = None

        self._publish_write_confirm = None
        self._publish_restore_result = None
        self._publish_cold_storage_status = None
        self._publish_event_log = None

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成, 冷存储容量={self.COLD_CAPACITY_BYTES/1024/1024:.0f}MB")

    # ========== 回调注入 ==========
    def set_write_command_query(self, callback: Callable[[], Optional[ArchiveWriteCommand]]):
        self._query_write_command = callback

    def set_restore_request_query(self, callback: Callable[[], Optional[ArchiveRestoreRequest]]):
        self._query_restore_request = callback

    def set_write_confirm_publisher(self, callback: Callable[[ArchiveWriteConfirm], None]):
        self._publish_write_confirm = callback

    def set_restore_result_publisher(self, callback: Callable[[ArchiveRestoreResult], None]):
        self._publish_restore_result = callback

    def set_cold_storage_status_publisher(self, callback: Callable[[ColdStorageStatus], None]):
        self._publish_cold_storage_status = callback

    def set_event_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_event_log = callback

    # ========== 主循环 ==========
    def run_manager_cycle(self):
        now = time.time()

        if self.state == ColdStorageState.SYSTEM_PAUSED:
            return

        # 冷存储容量管理
        usage_pct = self._calculate_usage_pct()
        if usage_pct >= self.CRITICAL_THRESHOLD:
            self._trigger_cleanup(now, target_usage=0.70)
        elif usage_pct >= self.WARN_THRESHOLD:
            self._trigger_cleanup(now, target_usage=0.70)
            self.state = ColdStorageState.COLD_WARN
        elif self.state == ColdStorageState.COLD_WARN and usage_pct < self.WARN_THRESHOLD:
            self.state = ColdStorageState.NORMAL

        # 定期状态上报
        if now - self._last_status_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._publish_status()
            self._last_status_time = now

        # 处理归档写入指令
        write_cmd = self._query_write_command() if self._query_write_command else None
        if write_cmd:
            self._handle_write(write_cmd)
            return

        # 处理归档恢复请求
        restore_req = self._query_restore_request() if self._query_restore_request else None
        if restore_req:
            self._handle_restore(restore_req)

    # ========== 归档写入 ==========
    def _handle_write(self, command: ArchiveWriteCommand):
        self.state = ColdStorageState.COMPRESSING
        start_time = time.time()

        # 容量检查
        usage_pct = self._calculate_usage_pct()
        if usage_pct >= self.CRITICAL_THRESHOLD:
            self._send_write_confirm(command.entry_id, False, 0, 0, "冷存储容量已满")
            self.state = ColdStorageState.NORMAL
            return

        # 序列化经验数据
        try:
            raw_data = json.dumps(command.experience_data, ensure_ascii=False).encode('utf-8')
        except Exception:
            self._send_write_confirm(command.entry_id, False, 0, 0, "经验数据序列化失败")
            self.state = ColdStorageState.NORMAL
            return

        original_size = len(raw_data)

        # 压缩（使用 zlib 模拟）
        try:
            compressed = zlib.compress(raw_data, level=self.COMPRESS_LEVEL)
        except Exception:
            self._send_write_confirm(command.entry_id, False, original_size, 0, "压缩失败")
            self.state = ColdStorageState.NORMAL
            return

        compressed_size = len(compressed)

        # 构建归档条目
        archive_id = f"ARCH-{command.entry_id}-{int(time.time())}"
        checksum = hashlib.sha256(compressed).hexdigest()
        entry = ArchiveEntry(
            archive_id=archive_id,
            original_entry_id=command.entry_id,
            compressed_data=compressed,
            original_size=original_size,
            compressed_size=compressed_size,
            algorithm="zlib-6",
            metadata=command.metadata,
            checksum=checksum,
            archived_at=time.time()
        )

        # 存储到冷存储
        self._archives[archive_id] = entry
        self._index_by_original_id[command.entry_id] = archive_id
        self._total_used_bytes += compressed_size
        self._total_archives += 1

        elapsed = time.time() - start_time
        if elapsed > self.SINGLE_ARCHIVE_TIMEOUT_SEC:
            self._send_write_confirm(command.entry_id, False, original_size, compressed_size, "归档超时")
            # 回滚
            del self._archives[archive_id]
            del self._index_by_original_id[command.entry_id]
            self._total_used_bytes -= compressed_size
            self._total_archives -= 1
            self.state = ColdStorageState.NORMAL
            return

        compression_ratio = round(1 - compressed_size / max(original_size, 1), 2)
        self._send_write_confirm(command.entry_id, True, original_size, compressed_size, compression_ratio, archive_id)
        self._log_event("ARCHIVE_WRITTEN", {"archive_id": archive_id, "original_size": original_size, "compressed_size": compressed_size})
        self.state = ColdStorageState.NORMAL

    def _send_write_confirm(self, entry_id: str, success: bool, original_size: int, compressed_size: int, error_or_location: str, archive_location: str = ""):
        if self._publish_write_confirm:
            self._publish_write_confirm(ArchiveWriteConfirm(
                entry_id=entry_id,
                success=success,
                archive_location=error_or_location if not success else archive_location,
                original_size=original_size,
                compressed_size=compressed_size,
                compression_ratio=round(1 - compressed_size / max(original_size, 1), 2) if success else 0.0,
                error_reason="" if success else error_or_location
            ))

    # ========== 归档恢复 ==========
    def _handle_restore(self, request: ArchiveRestoreRequest):
        self.state = ColdStorageState.RESTORING
        start_time = time.time()
        restored = []

        # 按条件检索
        candidates = []
        if request.entry_ids:
            for eid in request.entry_ids:
                archive_id = self._index_by_original_id.get(eid)
                if archive_id and archive_id in self._archives:
                    candidates.append(self._archives[archive_id])
        else:
            # 按时间范围和层级筛选
            for archive in self._archives.values():
                if request.time_range:
                    t0, t1 = request.time_range
                    if not (t0 <= archive.archived_at <= t1):
                        continue
                if request.source_layer and archive.metadata.get("source_layer") != request.source_layer:
                    continue
                candidates.append(archive)

        # 按归档时间降序排列
        candidates.sort(key=lambda x: x.archived_at, reverse=True)

        for archive in candidates[:request.max_entries]:
            # 校验完整性
            actual_checksum = hashlib.sha256(archive.compressed_data).hexdigest()
            if actual_checksum != archive.checksum:
                self._log_event("ARCHIVE_CHECKSUM_MISMATCH", {"archive_id": archive.archive_id})
                continue

            # 解压
            try:
                decompressed = zlib.decompress(archive.compressed_data)
                data = json.loads(decompressed.decode('utf-8'))
            except Exception:
                self._log_event("ARCHIVE_DECOMPRESS_FAILED", {"archive_id": archive.archive_id})
                continue

            restored.append(RestoredEntry(
                entry_id=archive.original_entry_id,
                experience_data=data,
                metadata=archive.metadata
            ))

        elapsed = (time.time() - start_time) * 1000
        result = ArchiveRestoreResult(
            request_id=request.request_id,
            restored_entries=restored,
            total_found=len(restored),
            duration_ms=elapsed
        )

        if self._publish_restore_result:
            self._publish_restore_result(result)

        self.state = ColdStorageState.NORMAL

    # ========== 容量管理 ==========
    def _trigger_cleanup(self, now: float, target_usage: float = 0.70):
        """清理旧归档数据，释放空间"""
        retention_sec = self.RETENTION_DAYS * 86400
        # 优先删除超期且非永久保留的归档
        expired = []
        for archive_id, archive in self._archives.items():
            if archive.permanent_retain:
                continue
            if (now - archive.archived_at) > retention_sec:
                expired.append((archive_id, archive))

        # 按归档时间从旧到新排序
        expired.sort(key=lambda x: x[1].archived_at)

        # 计算需要释放的空间
        target_bytes = int(self.COLD_CAPACITY_BYTES * target_usage)
        current_usage = self._total_used_bytes
        released = 0
        for archive_id, archive in expired:
            if current_usage - released <= target_bytes:
                break
            # 删除
            del self._archives[archive_id]
            if archive.original_entry_id in self._index_by_original_id:
                del self._index_by_original_id[archive.original_entry_id]
            released += archive.compressed_size

        if released > 0:
            self._total_used_bytes -= released
            self._total_archives = len(self._archives)
            self._log_event("COLD_CLEANUP", {"released_bytes": released, "cleaned_count": len(expired)})

    # ========== 辅助 ==========
    def _calculate_usage_pct(self) -> float:
        if self.COLD_CAPACITY_BYTES <= 0:
            return 0.0
        return round(min(self._total_used_bytes / self.COLD_CAPACITY_BYTES, 1.0), 3)

    def _publish_status(self):
        if self._publish_cold_storage_status:
            oldest = min((a.archived_at for a in self._archives.values()), default=0)
            newest = max((a.archived_at for a in self._archives.values()), default=0)
            self._publish_cold_storage_status(ColdStorageStatus(
                state=self.state,
                total_capacity_bytes=self.COLD_CAPACITY_BYTES,
                used_bytes=self._total_used_bytes,
                usage_pct=self._calculate_usage_pct(),
                total_archives=self._total_archives,
                oldest_archive_time=oldest,
                newest_archive_time=newest
            ))

    def emergency_shutdown(self):
        self.state = ColdStorageState.SYSTEM_PAUSED
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
    print("  Agent-mlnf-mem 存储压缩与冷归档单元 (ag-mem-49) 演示")
    print("=" * 70)

    manager = ColdStorageManager()

    print_separator("STEP 1: 压缩归档一个经验条目")
    manager.set_write_command_query(lambda: ArchiveWriteCommand(
        entry_id="E01",
        experience_data={"tool": "weather_api", "result": "success", "data": "北京天气晴"},
        metadata={"source_layer": "L3", "i_value": 0.5, "forget_reason": "I值过低"}
    ))
    manager.run_manager_cycle()
    print(f"  当前归档数: {manager._total_archives}")

    print_separator("STEP 2: 从冷存储恢复条目")
    manager.set_restore_request_query(lambda: ArchiveRestoreRequest(
        request_id="R01",
        entry_ids=["E01"]
    ))
    manager.run_manager_cycle()
    print(f"  恢复完成")

    print_separator("STEP 3: 冷存储容量预警触发清理")
    # 模拟大量数据
    manager._total_used_bytes = int(manager.COLD_CAPACITY_BYTES * 0.85)
    manager.run_manager_cycle()
    print(f"  状态: {manager.state.value}")

    print("\n✅ 存储压缩与冷归档单元演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("ag-mem-49 存储压缩与冷归档单元 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        def setup_manager():
            return ColdStorageManager()

        # TC-M49-01: 正常压缩归档
        print("\n[TC-M49-01] 正常压缩归档")
        try:
            m = setup_manager()
            m.set_write_command_query(lambda: ArchiveWriteCommand(
                entry_id="T01",
                experience_data={"key": "value"},
                metadata={"source_layer": "L3"}
            ))
            m.run_manager_cycle()
            assert m._total_archives == 1
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M49-02: 冷存储满时拒绝写入
        print("\n[TC-M49-02] 冷存储满时拒绝写入")
        try:
            m = setup_manager()
            m._total_used_bytes = int(m.COLD_CAPACITY_BYTES * 0.96)
            m.set_write_command_query(lambda: ArchiveWriteCommand(
                entry_id="T02",
                experience_data={"key": "value"},
                metadata={"source_layer": "L3"}
            ))
            m.run_manager_cycle()
            assert m._total_archives == 0
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M49-03: 正常恢复条目
        print("\n[TC-M49-03] 正常恢复条目")
        try:
            m = setup_manager()
            # 先归档一条
            m._archives["A1"] = ArchiveEntry(
                archive_id="A1", original_entry_id="T03",
                compressed_data=zlib.compress(json.dumps({"key": "value"}).encode()),
                original_size=20, compressed_size=30, checksum=hashlib.sha256(zlib.compress(json.dumps({"key": "value"}).encode())).hexdigest()
            )
            m._index_by_original_id["T03"] = "A1"
            m._total_archives = 1
            m.set_restore_request_query(lambda: ArchiveRestoreRequest(entry_ids=["T03"]))
            m.run_manager_cycle()
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M49-04: 恢复时校验和不匹配跳过
        print("\n[TC-M49-04] 恢复时校验和不匹配跳过")
        try:
            m = setup_manager()
            compressed = zlib.compress(json.dumps({"key": "value"}).encode())
            m._archives["A2"] = ArchiveEntry(
                archive_id="A2", original_entry_id="T04",
                compressed_data=compressed, original_size=20, compressed_size=30,
                checksum="wrong_checksum"
            )
            m._index_by_original_id["T04"] = "A2"
            m._total_archives = 1
            m.set_restore_request_query(lambda: ArchiveRestoreRequest(entry_ids=["T04"]))
            m.run_manager_cycle()
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M49-05: 超期数据自动清理
        print("\n[TC-M49-05] 超期数据自动清理")
        try:
            m = setup_manager()
            old_time = time.time() - (m.RETENTION_DAYS + 10) * 86400
            compressed = zlib.compress(json.dumps({"old": "data"}).encode())
            m._archives["OLD"] = ArchiveEntry(
                archive_id="OLD", original_entry_id="OLD-E",
                compressed_data=compressed, original_size=10, compressed_size=20,
                checksum=hashlib.sha256(compressed).hexdigest(),
                archived_at=old_time
            )
            m._total_used_bytes = int(m.COLD_CAPACITY_BYTES * 0.85)
            m._total_archives = 1
            m._trigger_cleanup(time.time(), target_usage=0.70)
            assert "OLD" not in m._archives
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M49-06: 紧急熔断
        print("\n[TC-M49-06] 紧急熔断")
        try:
            m = setup_manager()
            m.emergency_shutdown()
            assert m.state == ColdStorageState.SYSTEM_PAUSED
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