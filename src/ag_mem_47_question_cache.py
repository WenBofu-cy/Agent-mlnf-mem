#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-47
模块名称: 疑问缓存库
所属分区: 四、漏斗外挂扩展区（物理隔离）
核心职责: 作为漏斗外挂扩展区的临时疑问存储库，暂存 ECC 认知大脑在推理过程中产生的推理
          断点、未确认认知项、低置信度场景数据。当 ECC 模块（如 ag-ecc-08 元认知模块）
          判定当前场景的置信度低于阈值或存在无法即时解决的逻辑矛盾时，将相关数据写入疑问
          缓存库，供系统离线复盘、根因分析、策略修正与模型迭代使用。本库完全独立于双漏斗
          记忆系统运行，不参与记忆沉淀、筛选、晋升与遗忘机制。仅提供暂存与查询服务，缓存
          条目在系统确认问题已解决或超过保留期限后自动清除。

依赖模块: 无
被依赖模块: ag-ecc-08, ag-ecc-03, ag-mem-01

安全约束:
  C-01: 疑问缓存条目在系统确认问题解决或超过保留期限后自动清除，不得无限期保留
  C-02: 疑问条目中的场景描述不得包含用户的原始输入文本或个人身份信息，仅保留场景特征
         哈希与结构化的上下文参数
  C-03: 本库完全独立于漏斗记忆系统，不参与记忆沉淀、晋升、遗忘任何环节
  C-04: 疑问缓存数据仅用于离线复盘与系统调试，不得作为实时决策依据

版本: V1.0 (总线集成版)
"""

import time
import uuid
from typing import Any, Dict, List, Optional
from enum import Enum

from memory_bus import InternalBus, Message


class CacheState(Enum):
    NORMAL_SERVICE = "normal_service"
    CAPACITY_WARNING = "capacity_warning"
    CAPACITY_FULL = "capacity_full"
    SYSTEM_PAUSED = "system_paused"


class QuestionType(Enum):
    REASONING_BREAK = "推理断点"
    LOGIC_CONTRADICTION = "逻辑矛盾"
    LOW_CONFIDENCE = "低置信度场景"


class ProcessingStatus(Enum):
    PENDING = "待分析"
    ANALYZING = "分析中"
    RESOLVED = "已解决"
    IGNORED = "已忽略"
    EXPIRED = "已过期"


class QuestionCache:
    module_id = "ag-mem-47"
    module_name = "疑问缓存库"
    version = "V1.0"

    MAX_ENTRIES = 5000
    WARNING_THRESHOLD = 0.8
    RETENTION_DAYS = 90
    CLEANUP_INTERVAL_SEC = 86400
    STATUS_REPORT_INTERVAL_SEC = 120

    def __init__(self):
        self.bus: Optional[InternalBus] = None
        self.state = CacheState.NORMAL_SERVICE
        self._entries: Dict[str, Dict[str, Any]] = {}
        self._total_entries: int = 0
        self._last_cleanup_time: float = time.time()
        self._last_status_time: float = time.time()
        self._pending_logs: List[Dict[str, Any]] = []

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成, 最大容量={self.MAX_ENTRIES}")

    # ====================== 主循环 ======================
    def question_cache_main_loop(self):
        if self.state == CacheState.SYSTEM_PAUSED:
            return

        if self.bus:
            self.bus.process_batch(10)

        now = time.time()

        # 定期清理过期条目
        if now - self._last_cleanup_time >= self.CLEANUP_INTERVAL_SEC:
            self._perform_cleanup(now)
            self._last_cleanup_time = now

        # 定期状态上报
        if now - self._last_status_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._report_status()
            self._last_status_time = now

    # ====================== 总线消息入口 ======================
    def handle_message(self, msg: Message):
        if not isinstance(msg.data, dict):
            return

        # 提交疑问
        if msg.topic == "ag-mem-47.question_submit":
            self._handle_submit(msg)
            return

        # 查询疑问
        if msg.topic == "ag-mem-47.question_query":
            self._handle_query(msg)
            return

        # 更新疑问状态
        if msg.topic == "ag-mem-47.question_update":
            self._handle_update(msg)
            return

    def _handle_submit(self, msg: Message):
        """处理疑问提交"""
        if self._total_entries >= self.MAX_ENTRIES:
            self._reply_submit(msg, "", False, "缓存已满")
            return

        data = msg.data
        entry_id = f"Q-{int(time.time()*1000)}-{uuid.uuid4().hex[:8]}"
        entry = {
            "entry_id": entry_id,
            "source_module": data.get("source_module", msg.source_module),
            "question_type": data.get("question_type", "低置信度场景"),
            "related_entry_ids": data.get("related_entry_ids", []),
            "scene_snapshot_hash": data.get("scene_snapshot_hash", ""),
            "context_summary": data.get("context_summary", {}),
            "current_confidence": data.get("current_confidence", 0.0),
            "priority": data.get("priority", "中"),
            "status": "待分析",
            "resolution_summary": "",
            "created_at": time.time(),
            "updated_at": time.time()
        }
        self._entries[entry_id] = entry
        self._total_entries += 1

        # 容量检查
        if self._total_entries >= self.MAX_ENTRIES:
            self.state = CacheState.CAPACITY_FULL
        elif self._total_entries >= self.MAX_ENTRIES * self.WARNING_THRESHOLD:
            self.state = CacheState.CAPACITY_WARNING

        self._reply_submit(msg, entry_id, True, "")

    def _handle_query(self, msg: Message):
        """处理疑问查询"""
        data = msg.data
        question_type = data.get("question_type")
        status = data.get("status")
        time_range = data.get("time_range")
        max_results = data.get("max_results", 50)

        matched = []
        now = time.time()
        for entry in self._entries.values():
            if question_type and entry.get("question_type") != question_type:
                continue
            if status and entry.get("status") != status:
                continue
            if time_range and (now - entry.get("created_at", now)) > time_range:
                continue
            matched.append(entry)

        matched.sort(key=lambda x: x.get("created_at", 0), reverse=True)
        matched = matched[:max_results]

        if self.bus:
            self.bus.publish(
                topic=f"{msg.source_module}.question_query_result",
                source_module=self.module_id,
                data={"entries": matched, "total_matched": len(matched)},
                target_module=msg.source_module,
                correlation_id=msg.correlation_id
            )

    def _handle_update(self, msg: Message):
        """处理疑问状态更新"""
        data = msg.data
        entry_id = data.get("entry_id", "")
        new_status = data.get("new_status", "已解决")
        resolution = data.get("resolution_summary", "")

        if entry_id in self._entries:
            self._entries[entry_id]["status"] = new_status
            self._entries[entry_id]["resolution_summary"] = resolution
            self._entries[entry_id]["updated_at"] = time.time()
            self._reply_update(msg, entry_id, True)
        else:
            self._reply_update(msg, entry_id, False)

    def _reply_submit(self, msg: Message, entry_id: str, success: bool, error: str):
        if self.bus:
            self.bus.publish(
                topic=f"{msg.source_module}.question_submit_confirm",
                source_module=self.module_id,
                data={"entry_id": entry_id, "success": success, "error": error},
                target_module=msg.source_module,
                correlation_id=msg.correlation_id
            )

    def _reply_update(self, msg: Message, entry_id: str, success: bool):
        if self.bus:
            self.bus.publish(
                topic=f"{msg.source_module}.question_update_confirm",
                source_module=self.module_id,
                data={"entry_id": entry_id, "success": success},
                target_module=msg.source_module,
                correlation_id=msg.correlation_id
            )

    # ====================== 定期清理 ======================
    def _perform_cleanup(self, now: float):
        retention_sec = self.RETENTION_DAYS * 86400
        expired = [
            eid for eid, entry in self._entries.items()
            if (now - entry.get("created_at", now)) > retention_sec
            and entry.get("status") in ("已解决", "已忽略")
        ]
        for eid in expired:
            del self._entries[eid]
            self._total_entries -= 1

        if self._total_entries < self.MAX_ENTRIES * self.WARNING_THRESHOLD:
            self.state = CacheState.NORMAL_SERVICE

    # ====================== 状态上报 ======================
    def _report_status(self):
        if self.bus:
            usage = self._total_entries / self.MAX_ENTRIES if self.MAX_ENTRIES > 0 else 0
            self.bus.publish_to_module(
                target_module="ag-mem-03",
                event_type="internal_status",
                source_module=self.module_id,
                data={
                    "state": self.state.value,
                    "total_entries": self._total_entries,
                    "usage_pct": round(usage, 2)
                }
            )

    # ====================== 管理接口 ======================
    def emergency_shutdown(self):
        self.state = CacheState.SYSTEM_PAUSED
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