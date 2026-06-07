#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-28
模块名称: L5核心层存储单元
所属分区: 三、漏斗二：任务经验漏斗 / 五层存储
核心职责: 作为漏斗二五层记忆存储架构的第五层（最高层），专门存储终身不可遗忘的安全底线
          经验、不可抗力事件及经人工锁定或高安全显著性（S≥0.9）直达写入的关键策略。
          L5是整个记忆中枢的"永久记忆区"，占漏斗二总容量的0.5%，默认情况下物理锁定，
          写入权限由ag-mem-29管控，访问权限由ag-mem-30管控。所有L5层条目默认不可删除、
          不可修改，仅提供只读查询服务。不参与晋升判定或遗忘策略，仅执行高安全经验的接收、
          存储与只读检索。

依赖模块:
    ag-mem-16, ag-mem-27, ag-mem-29, ag-mem-30, ag-mem-48
被依赖模块:
    ag-mem-29, ag-mem-30, ag-mem-15~19

安全约束:
  S-01: L5层默认处于物理写保护锁定状态，任何写入操作必须持有ag-mem-29签发的有效临时解锁令牌
  S-02: L5层条目永久保留，不受任何遗忘策略约束，不得通过ag-mem-40或ag-mem-42自动删除
  S-03: L5层条目仅可人工删除（需ag-mem-29双重确认），任何自动化模块无权修改或删除L5数据
  S-04: L5层查询必须通过ag-mem-30的令牌验证，禁止无令牌访问
  S-05: 临时解锁令牌有效期30秒，超时自动作废，L5自动恢复锁定状态
  S-06: L5写入来源仅限"S值直达"、"L4推送"、"人工锁定"三种，其他来源一律拒绝
  S-07: S值直达写入必须满足S ≥ 0.9且结果标签为成功，失败经验不得通过S值直达进入L5

版本: V1.0
"""

import time
import uuid
from typing import Any, Dict, List, Optional
from enum import Enum

from memory_bus import InternalBus, Message


class StorageState(Enum):
    LOCKED_NORMAL = "locked_normal"
    TEMP_UNLOCKED = "temp_unlocked"
    CAPACITY_FULL = "capacity_full"
    SYSTEM_PAUSED = "system_paused"


class L5CoreStorage:
    module_id = "ag-mem-28"
    module_name = "L5核心层存储单元"
    version = "V1.0"

    L5_CAPACITY_RATIO = 0.005
    MAX_ENTRIES = 200
    MAX_ENTRY_SIZE_BYTES = 30 * 1024
    CAPACITY_CRITICAL_THRESHOLD = 0.95
    TEMP_UNLOCK_MAX_DURATION_SEC = 30
    STATUS_REPORT_INTERVAL_SEC = 120

    def __init__(self):
        self.bus: Optional[InternalBus] = None
        self.state = StorageState.LOCKED_NORMAL

        self._entries: Dict[str, Dict[str, Any]] = {}
        self._entry_count: int = 0
        self._temp_unlock_token: Optional[Dict[str, Any]] = None
        self._token_write_remaining: int = 0
        self._unlock_start_time: float = 0.0
        self._last_write_time: float = 0.0
        self._last_status_time: float = time.time()
        self._pending_queries: Dict[str, Dict[str, Any]] = {}
        self._pending_logs: List[Dict[str, Any]] = []

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成, "
              f"默认锁定状态, 最大条目={self.MAX_ENTRIES}")

    # ====================== 统一主循环入口 ======================
    def run_cycle(self):
        self.l5_storage_main_loop()

    def l5_storage_main_loop(self):
        if self.state == StorageState.SYSTEM_PAUSED:
            return

        if self.bus:
            self.bus.process_batch(10)

        now = time.time()

        # 检查临时解锁超时
        if self.state == StorageState.TEMP_UNLOCKED:
            if now - self._unlock_start_time >= self.TEMP_UNLOCK_MAX_DURATION_SEC:
                self._restore_lock("超时自动恢复")

        # 定期状态上报
        if now - self._last_status_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._report_status()
            self._last_status_time = now

        # 清理超时的查询令牌验证请求
        self._cleanup_pending_queries(now)

    # ====================== 总线消息入口 ======================
    def handle_message(self, msg: Message):
        if not isinstance(msg.data, dict):
            return

        if msg.topic == "ag-mem-28.experience_write":
            self._handle_write(msg)
        elif msg.topic == "ag-mem-28.experience_query":
            self._handle_query(msg)
        elif msg.topic == "ag-mem-28.temp_unlock_token":
            self._handle_unlock_token(msg)
        elif msg.topic == "ag-mem-28.lock_state_change":
            if msg.data.get("new_lock_state") == "LOCKED":
                self._restore_lock(msg.data.get("reason", "外部指令"))
        elif msg.topic == "ag-mem-28.token_validation_result":
            self._handle_token_validation_result(msg)

    def _handle_write(self, msg: Message):
        """处理写入请求"""
        data = msg.data
        entry_data = data.get("entry", {})
        write_source = data.get("write_source", "")
        security_token = data.get("security_token", "")

        # 检查锁定状态
        if self.state != StorageState.TEMP_UNLOCKED:
            self._reject_write(msg, "L5处于锁定状态，写入需临时解锁令牌")
            return

        # 验证写入令牌与当前解锁令牌匹配
        if not self._temp_unlock_token or security_token != self._temp_unlock_token.get("token_id", ""):
            self._reject_write(msg, "无效的临时解锁令牌")
            return

        # 检查令牌写入配额
        if self._token_write_remaining <= 0:
            self._restore_lock("写入配额耗尽")
            self._reject_write(msg, "令牌写入配额已耗尽")
            return

        # 校验写入来源
        if write_source not in ("S值直达", "L4推送", "人工锁定"):
            self._reject_write(msg, "非法写入来源")
            return

        # S值直达校验
        if write_source == "S值直达":
            s_val = float(entry_data.get("s_value", 0))
            if s_val < 0.9:
                self._reject_write(msg, f"S值不满足L5直达条件（当前={s_val:.2f}，要求≥0.9）")
                return
            if entry_data.get("result_label") != "成功":
                self._reject_write(msg, "失败经验不得通过S值直达进入L5")
                return

        # L4推送校验
        if write_source == "L4推送":
            if float(entry_data.get("i_value", 0)) < 0.85:
                self._reject_write(msg, "置信度不满足L5推送条件")
                return

        # 容量检查
        usage_pct = self._calculate_usage_pct()
        if usage_pct >= self.CAPACITY_CRITICAL_THRESHOLD or self._entry_count >= self.MAX_ENTRIES:
            self.state = StorageState.CAPACITY_FULL
            self._reject_write(msg, "L5容量已满或达到最大条目数上限")
            return

        # 写入
        entry_id = entry_data.get("entry_id", f"L5-{uuid.uuid4().hex[:8]}")
        entry = {
            "entry_id": entry_id,
            "source_slot_id": entry_data.get("source_slot_id", ""),
            "write_source": write_source,
            "experience_data": entry_data.get("experience_data", {}),
            "i_value": float(entry_data.get("i_value", 0)),
            "s_value": float(entry_data.get("s_value", 0)),
            "v_value": float(entry_data.get("v_value", 0)),
            "c_value": float(entry_data.get("c_value", 0)),
            "result_label": entry_data.get("result_label", "成功"),
            "readonly": True,
            "editable": False,
            "deletable": False,
            "locked_at": time.time(),
            "timestamp": time.time()
        }

        self._entries[entry_id] = entry
        self._entry_count += 1
        self._last_write_time = time.time()

        self._token_write_remaining -= 1
        if self._token_write_remaining <= 0:
            self._restore_lock("写入配额耗尽")

        self._reply_write_success(msg, entry_id)

    def _handle_query(self, msg: Message):
        """处理查询请求（S-04：必须通过ag-mem-30令牌验证）"""
        query_token = msg.data.get("query_token", "")
        if not query_token:
            self._reject_query(msg, "缺少ag-mem-30签发的查询令牌")
            return

        # 向ag-mem-30发起令牌验证
        corr_id = str(uuid.uuid4())
        self._pending_queries[corr_id] = {
            "original_msg": msg,
            "start_time": time.time()
        }

        if self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-30",
                event_type="validate_token",
                source_module=self.module_id,
                data={
                    "token": query_token,
                    "_correlation_id": corr_id
                }
            )

    def _handle_token_validation_result(self, msg: Message):
        """处理ag-mem-30的令牌验证回执"""
        corr_id = msg.data.get("_correlation_id", "")
        pending = self._pending_queries.pop(corr_id, None)
        if not pending:
            return

        original_msg = pending["original_msg"]
        if not msg.data.get("valid", False):
            self._reject_query(original_msg, f"令牌验证失败：{msg.data.get('reason', '无效令牌')}")
            return

        # 令牌有效，执行查询
        conditions = original_msg.data.get("query", {})
        keywords = conditions.get("keywords", [])
        max_results = original_msg.data.get("max_results", 20)

        matched = []
        for entry in self._entries.values():
            if keywords:
                text = str(entry.get("experience_data", ""))
                if not any(kw in text for kw in keywords):
                    continue
            matched_entry = entry.copy()
            matched_entry["readonly"] = True
            matched_entry["editable"] = False
            matched_entry["deletable"] = False
            matched.append(matched_entry)

        matched.sort(key=lambda x: x.get("i_value", 0), reverse=True)
        matched = matched[:max_results]

        if self.bus:
            self.bus.publish(
                topic=f"{original_msg.source_module}.query_response",
                source_module=self.module_id,
                data={
                    "success": True,
                    "matched_experiences": matched,
                    "layer": "L5",
                    "total_count": len(matched)
                },
                target_module=original_msg.source_module,
                correlation_id=original_msg.correlation_id
            )

    def _handle_unlock_token(self, msg: Message):
        """接收临时解锁令牌（仅接受来自ag-mem-29的令牌）"""
        if msg.source_module != "ag-mem-29":
            self._log_event("UNLOCK_TOKEN_REJECTED", {"reason": "非法来源"})
            return

        data = msg.data
        self._temp_unlock_token = data
        self._token_write_remaining = data.get("max_write_entries", 1)
        self._unlock_start_time = time.time()
        self.state = StorageState.TEMP_UNLOCKED
        self._log_event("TEMP_UNLOCKED", {"max_write_entries": self._token_write_remaining})

    def _reject_write(self, msg: Message, reason: str):
        if self.bus:
            self.bus.publish(
                topic=f"{msg.source_module}.write_receipt",
                source_module=self.module_id,
                data={"success": False, "error_reason": reason, "l5_state": self.state.value},
                target_module=msg.source_module,
                correlation_id=msg.correlation_id
            )
        self._log_event("WRITE_REJECTED", {"reason": reason})

    def _reject_query(self, msg: Message, reason: str):
        if self.bus:
            self.bus.publish(
                topic=f"{msg.source_module}.query_response",
                source_module=self.module_id,
                data={
                    "success": False,
                    "error_reason": reason,
                    "matched_experiences": [],
                    "total_count": 0
                },
                target_module=msg.source_module,
                correlation_id=msg.correlation_id
            )
        self._log_event("QUERY_REJECTED", {"reason": reason})

    def _reply_write_success(self, msg: Message, entry_id: str):
        if self.bus:
            self.bus.publish(
                topic=f"{msg.source_module}.write_receipt",
                source_module=self.module_id,
                data={
                    "success": True,
                    "entry_id": entry_id,
                    "l5_usage_pct": round(self._calculate_usage_pct(), 3),
                    "lock_status_restored": self.state == StorageState.LOCKED_NORMAL
                },
                target_module=msg.source_module,
                correlation_id=msg.correlation_id
            )

    def _restore_lock(self, reason: str):
        self.state = StorageState.LOCKED_NORMAL
        self._temp_unlock_token = None
        self._token_write_remaining = 0
        self._log_event("LOCK_RESTORED", {"reason": reason})

    def _cleanup_pending_queries(self, now: float):
        timeout = 5.0  # 查询令牌验证超时
        expired = [cid for cid, p in self._pending_queries.items() if now - p["start_time"] > timeout]
        for cid in expired:
            pending = self._pending_queries.pop(cid)
            self._reject_query(pending["original_msg"], "令牌验证超时")

    def _calculate_usage_pct(self) -> float:
        if self.MAX_ENTRIES == 0:
            return 0.0
        return min(self._entry_count / self.MAX_ENTRIES, 1.0)

    def _report_status(self):
        if self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-48",
                event_type="storage_status",
                source_module=self.module_id,
                data={
                    "state": self.state.value,
                    "total_entries": self._entry_count,
                    "usage_pct": self._calculate_usage_pct(),
                    "lock_status": "锁定" if self.state == StorageState.LOCKED_NORMAL else "临时解锁",
                    "last_write_time": self._last_write_time,
                    "pending_token_validations": len(self._pending_queries)
                }
            )

    def get_entry_count(self) -> int:
        return self._entry_count

    # ====================== 管理接口 ======================
    def emergency_shutdown(self):
        self.state = StorageState.SYSTEM_PAUSED
        # 安全规范：紧急停机清空所有内存状态
        self._entries.clear()
        self._entry_count = 0
        self._temp_unlock_token = None
        self._token_write_remaining = 0
        self._pending_queries.clear()
        self._pending_logs.clear()
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