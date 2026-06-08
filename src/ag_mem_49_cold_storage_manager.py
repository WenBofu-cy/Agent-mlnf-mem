#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-49
模块名称: 存储压缩与冷归档单元
所属分区: 五、存储与系统运维
核心职责: 接收 ag-mem-42 发送的归档写入指令，对 L3 及以上层级的被遗忘经验条目执行压缩打包
          与冷归档存储。将经验数据及其元数据序列化后，使用 zlib 压缩写入冷存储分区，并返回
          存储位置与压缩后大小。管理冷存储分区的容量，当冷存储逼近上限时触发旧归档数据的
          清理。支持按需从冷存储中恢复归档条目。不参与遗忘判定或删除决策，仅执行压缩、归档
          与恢复操作。

依赖模块: ag-mem-42, ag-mem-48
被依赖模块: ag-mem-42, ag-mem-48, ag-mem-51

安全约束:
  C-01: 归档数据采用 SHA-256 校验和，恢复时必须验证完整性
  C-02: 默认保留期限 1 年，超期优先清理（永久保留标记除外）
  C-03: 归档操作保证原子性
  C-04: 冷存储分区独立于热存储

版本: V1.0 (总线集成版)
"""

import time
import uuid
import hashlib
import json
import zlib
from typing import Any, Dict, List, Optional
from enum import Enum

from memory_bus import InternalBus, Message


class ColdStorageState(Enum):
    NORMAL = "normal"
    COMPRESSING = "compressing"
    RESTORING = "restoring"
    COLD_WARN = "cold_warn"
    SYSTEM_PAUSED = "system_paused"


class ColdStorageManager:
    module_id = "ag-mem-49"
    module_name = "存储压缩与冷归档单元"
    version = "V1.0"

    COLD_CAPACITY_BYTES = 50 * 1024 * 1024      # 50MB
    WARN_THRESHOLD = 0.80
    CRITICAL_THRESHOLD = 0.95
    RETENTION_DAYS = 365
    COMPRESS_LEVEL = 6
    STATUS_REPORT_INTERVAL_SEC = 120

    def __init__(self):
        self.bus: Optional[InternalBus] = None
        self.state = ColdStorageState.NORMAL
        self._archives: Dict[str, Dict[str, Any]] = {}
        self._index_by_original_id: Dict[str, str] = {}
        self._total_used_bytes: int = 0
        self._total_archives: int = 0
        self._last_status_time: float = time.time()
        self._pending_logs: List[Dict[str, Any]] = []

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成, "
              f"冷存储容量={self.COLD_CAPACITY_BYTES/1024/1024:.0f}MB")

    # ====================== 主循环 ======================
    def cold_storage_manager_main_loop(self):
        if self.state == ColdStorageState.SYSTEM_PAUSED:
            return

        if self.bus:
            self.bus.process_batch(10)

        now = time.time()

        # 容量管理
        usage_pct = self._calculate_usage_pct()
        if usage_pct >= self.CRITICAL_THRESHOLD or usage_pct >= self.WARN_THRESHOLD:
            self._trigger_cleanup(now, target_usage=0.70)
            if usage_pct >= self.WARN_THRESHOLD:
                self.state = ColdStorageState.COLD_WARN
            else:
                self.state = ColdStorageState.NORMAL
        else:
            if self.state == ColdStorageState.COLD_WARN:
                self.state = ColdStorageState.NORMAL

        # 定期状态上报
        if now - self._last_status_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._report_status()
            self._last_status_time = now

    # ====================== 总线消息入口 ======================
    def handle_message(self, msg: Message):
        if not isinstance(msg.data, dict):
            return

        # 归档写入指令（来自 ag-mem-42）
        if msg.topic == "ag-mem-49.archive_entry":
            self._handle_write(msg)
            return

        # 归档恢复请求（来自管理接口或 ag-mem-42）
        if msg.topic == "ag-mem-49.restore_request":
            self._handle_restore(msg)
            return

    def _handle_write(self, msg: Message):
        data = msg.data
        entry_id = data.get("entry_id", "")
        experience_data = data.get("experience_data", {})
        metadata = {
            "source_layer": data.get("source_layer", "L3"),
            "source_slot_id": data.get("source_slot_id", ""),
            "i_value": data.get("i_value", 0.0),
            "reason": data.get("reason", "遗忘判定")
        }

        self.state = ColdStorageState.COMPRESSING
        start_time = time.time()

        usage_pct = self._calculate_usage_pct()
        if usage_pct >= self.CRITICAL_THRESHOLD:
            self._reply_write_confirm(msg, entry_id, False, 0, 0, "冷存储容量已满")
            self.state = ColdStorageState.NORMAL
            return

        # 序列化
        try:
            raw_data = json.dumps(experience_data, ensure_ascii=False).encode('utf-8')
        except Exception:
            self._reply_write_confirm(msg, entry_id, False, 0, 0, "经验数据序列化失败")
            self.state = ColdStorageState.NORMAL
            return

        original_size = len(raw_data)

        # 压缩
        try:
            compressed = zlib.compress(raw_data, level=self.COMPRESS_LEVEL)
        except Exception:
            self._reply_write_confirm(msg, entry_id, False, original_size, 0, "压缩失败")
            self.state = ColdStorageState.NORMAL
            return

        compressed_size = len(compressed)
        archive_id = f"ARCH-{entry_id}-{int(time.time())}"
        checksum = hashlib.sha256(compressed).hexdigest()

        entry = {
            "archive_id": archive_id,
            "original_entry_id": entry_id,
            "compressed_data": compressed,
            "original_size": original_size,
            "compressed_size": compressed_size,
            "algorithm": "zlib-6",
            "metadata": metadata,
            "checksum": checksum,
            "permanent_retain": False,
            "archived_at": time.time()
        }

        # 保存
        self._archives[archive_id] = entry
        self._index_by_original_id[entry_id] = archive_id
        self._total_used_bytes += compressed_size
        self._total_archives += 1

        elapsed = time.time() - start_time
        if elapsed > self.SINGLE_ARCHIVE_TIMEOUT_SEC if hasattr(self, 'SINGLE_ARCHIVE_TIMEOUT_SEC') else 10:
            del self._archives[archive_id]
            del self._index_by_original_id[entry_id]
            self._total_used_bytes -= compressed_size
            self._total_archives -= 1
            self._reply_write_confirm(msg, entry_id, False, original_size, compressed_size, "归档超时")
            self.state = ColdStorageState.NORMAL
            return

        ratio = round(1 - compressed_size / max(original_size, 1), 2)
        self._reply_write_confirm(msg, entry_id, True, original_size, compressed_size, ratio, archive_id)
        self._log_event("ARCHIVE_WRITTEN", {"archive_id": archive_id, "ratio": ratio})
        self.state = ColdStorageState.NORMAL

    def _handle_restore(self, msg: Message):
        self.state = ColdStorageState.RESTORING
        data = msg.data
        entry_ids = data.get("entry_ids", [])
        max_entries = data.get("max_entries", 100)
        restored = []

        candidates = []
        if entry_ids:
            for eid in entry_ids:
                aid = self._index_by_original_id.get(eid)
                if aid and aid in self._archives:
                    candidates.append(self._archives[aid])
        else:
            candidates = list(self._archives.values())

        candidates.sort(key=lambda x: x["archived_at"], reverse=True)
        for archive in candidates[:max_entries]:
            if hashlib.sha256(archive["compressed_data"]).hexdigest() != archive["checksum"]:
                self._log_event("CHECKSUM_FAIL", {"archive_id": archive["archive_id"]})
                continue
            try:
                decompressed = zlib.decompress(archive["compressed_data"])
                exp_data = json.loads(decompressed.decode('utf-8'))
            except Exception:
                self._log_event("DECOMPRESS_FAIL", {"archive_id": archive["archive_id"]})
                continue
            restored.append({
                "entry_id": archive["original_entry_id"],
                "experience_data": exp_data,
                "metadata": archive["metadata"]
            })

        if self.bus:
            self.bus.publish(
                topic=f"{msg.source_module}.restore_result",
                source_module=self.module_id,
                data={"restored_entries": restored, "total_found": len(restored)},
                target_module=msg.source_module,
                correlation_id=msg.correlation_id
            )
        self.state = ColdStorageState.NORMAL

    def _trigger_cleanup(self, now: float, target_usage: float = 0.70):
        retention_sec = self.RETENTION_DAYS * 86400
        expired = [(aid, a) for aid, a in self._archives.items()
                   if not a.get("permanent_retain") and (now - a["archived_at"]) > retention_sec]
        expired.sort(key=lambda x: x[1]["archived_at"])
        target_bytes = int(self.COLD_CAPACITY_BYTES * target_usage)
        current = self._total_used_bytes
        released = 0
        for aid, archive in expired:
            if current - released <= target_bytes:
                break
            del self._archives[aid]
            if archive["original_entry_id"] in self._index_by_original_id:
                del self._index_by_original_id[archive["original_entry_id"]]
            released += archive["compressed_size"]
        if released:
            self._total_used_bytes -= released
            self._total_archives = len(self._archives)
            self._log_event("COLD_CLEANUP", {"released_bytes": released})

    def _reply_write_confirm(self, msg, eid, success, orig_sz, comp_sz, ratio_or_error, loc=""):
        if not self.bus:
            return
        data = {
            "entry_id": eid,
            "success": success,
            "original_size": orig_sz,
            "compressed_size": comp_sz,
        }
        if success:
            data["compression_ratio"] = ratio_or_error
            data["archive_location"] = loc
        else:
            data["error_reason"] = ratio_or_error if not success else ""
        self.bus.publish(
            topic=f"{msg.source_module}.archive_confirm",
            source_module=self.module_id,
            data=data,
            target_module=msg.source_module,
            correlation_id=msg.correlation_id
        )

    def _calculate_usage_pct(self) -> float:
        if self.COLD_CAPACITY_BYTES <= 0:
            return 0.0
        return round(min(self._total_used_bytes / self.COLD_CAPACITY_BYTES, 1.0), 3)

    def _report_status(self):
        if self.bus:
            oldest = min((a["archived_at"] for a in self._archives.values()), default=0)
            newest = max((a["archived_at"] for a in self._archives.values()), default=0)
            self.bus.publish_to_module(
                target_module="ag-mem-48",
                event_type="storage_status",
                source_module=self.module_id,
                data={
                    "state": self.state.value,
                    "total_capacity_bytes": self.COLD_CAPACITY_BYTES,
                    "used_bytes": self._total_used_bytes,
                    "usage_pct": self._calculate_usage_pct(),
                    "total_archives": self._total_archives,
                    "oldest_archive_time": oldest,
                    "newest_archive_time": newest
                }
            )

    def emergency_shutdown(self):
        self.state = ColdStorageState.SYSTEM_PAUSED
        self._log_event("SYSTEM_EVENT", {"sub_type": "emergency_shutdown"})

    def _log_event(self, event_type: str, details: Dict[str, Any]):
        log_entry = {
            "log_id": f"log-{uuid.uuid4().hex[:8]}",
            "event_type": event_type,
            "source_module": self.module_id,
            "details": details,
            "timestamp": time.time()
        }
        self._pending_logs.append(log_entry)
        if self.bus:
            self.bus.publish_to_module("ag-mem-51", "log_event", self.module_id, log_entry)

    def collect_pending_logs(self) -> List[Dict]:
        tmp = self._pending_logs.copy()
        self._pending_logs.clear()
        return tmp