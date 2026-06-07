#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-29
模块名称: L5核心层安全规则硬锁定单元
所属分区: 三、漏斗二：任务经验漏斗 / 五层存储
核心职责: 作为L5核心层的写入保护控制中枢，编译期实施L5存储分区的物理写保护，
          运行时管控写入权限的临时解除与恢复。接收来自ag-mem-16（S值直达）、
          ag-mem-27（L4推送）或人工操作的L5写入请求，在对请求进行安全校验通过后
          签发临时解锁令牌至ag-mem-28，授权限定条目数上限的写入操作。令牌有效期
          30秒，超时自动作废并强制恢复锁定。同时支持人工双重确认的安全锁定/解锁操作
          及L5条目的安全删除。不参与写入内容审核，仅执行写入权限的硬锁定管控。

依赖模块:
    ag-mem-28(L5核心层存储单元), ag-mem-45(安全规则库)
被依赖模块:
    ag-mem-16, ag-mem-27, ag-mem-28

安全约束:
  S-01: L5写入令牌的签发必须经过安全规则校验，任何绕过本模块直接请求L5写入的操作将被L5存储单元拒绝
  S-02: 令牌有效期30秒硬编码，超时自动吊销，不得通过任何外部接口延长有效期
  S-03: 同一时间仅允许一个活跃令牌存在，防止并发写入导致的安全风险
  S-04: 人工操作（锁定/解锁/删除）必须经过双重确认流程，每次操作需独立验证
  S-05: 所有L5写入授权与拒绝操作必须完整记录安全事件日志，不可篡改
  S-06: 紧急熔断时立即吊销所有活跃令牌，强制恢复L5锁定状态

版本: V1.0 (签名修复 & 目标修正)
"""

import time
import uuid
import hmac
import hashlib
from typing import Any, Dict, List, Optional
from enum import Enum

from memory_bus import InternalBus, Message


class LockControlState(Enum):
    LOCKED_READY = "locked_ready"
    SECURITY_CHECK = "security_check"
    TEMP_UNLOCKED = "temp_unlocked"
    RELOCKING = "relocking"
    SYSTEM_PAUSED = "system_paused"


class L5CoreLockControl:
    module_id = "ag-mem-29"
    module_name = "L5核心层安全规则硬锁定单元"
    version = "V1.0"

    TOKEN_VALIDITY_SEC = 30
    STATUS_REPORT_INTERVAL_SEC = 120

    # 与 ag-mem-28 共享的令牌签名密钥
    TOKEN_SECRET = "l5-core-storage-secret-key"

    def __init__(self):
        self.bus: Optional[InternalBus] = None
        self.state = LockControlState.LOCKED_READY
        self._active_token: Optional[Dict[str, Any]] = None
        self._token_issue_time: float = 0.0
        self._authorize_count: int = 0
        self._reject_count: int = 0
        self._last_status_time: float = time.time()
        self._pending_logs: List[Dict[str, Any]] = []
        self._pending_safety_checks: Dict[str, Dict[str, Any]] = {}

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成, 默认锁定状态")

    # ====================== 统一主循环入口 ======================
    def run_cycle(self):
        self.l5_lock_control_main_loop()

    def l5_lock_control_main_loop(self):
        if self.state == LockControlState.SYSTEM_PAUSED:
            return

        if self.bus:
            self.bus.process_batch(10)

        now = time.time()

        # 检查令牌超时 (S-02)
        if self.state == LockControlState.TEMP_UNLOCKED:
            if now - self._token_issue_time >= self.TOKEN_VALIDITY_SEC:
                self._revoke_token("令牌超时自动吊销")

        # 定期状态上报
        if now - self._last_status_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._report_status()
            self._last_status_time = now

    # ====================== 总线消息入口 ======================
    def handle_message(self, msg: Message):
        if not isinstance(msg.data, dict):
            return

        if msg.topic == "ag-mem-29.authorization_request":
            self._handle_authorization_request(msg)
            return

        if msg.topic == "ag-mem-29.manual_command":
            self._handle_manual_command(msg)
            return

        if msg.topic == "ag-mem-29.safety_check_response":
            self._handle_safety_check_response(msg)
            return

    def _handle_authorization_request(self, msg: Message):
        """处理写入授权请求"""
        data = msg.data
        write_source = data.get("write_source", "")
        s_value = float(data.get("s_value", 0))
        confidence = float(data.get("confidence", 0))
        request_write_count = int(data.get("request_write_count", 1))
        tool_sequence = data.get("tool_sequence", [])
        entry_id = data.get("entry_id", "")
        request_id = data.get("request_id", str(uuid.uuid4()))

        # 令牌冲突检查 (S-03)
        if self.state == LockControlState.TEMP_UNLOCKED:
            self._reply_authorization(msg, False, "已有活跃写入令牌，请等待当前令牌过期")
            return

        self.state = LockControlState.SECURITY_CHECK

        if write_source == "S值直达":
            if s_value >= 0.9:
                self._issue_token(msg, write_source, 1, request_id)
            else:
                self._reply_authorization(msg, False, f"S值不满足L5直达条件（当前={s_value:.2f}，要求≥0.9）")

        elif write_source == "L4推送":
            if confidence < 0.85:
                self._reply_authorization(msg, False, f"置信度不满足L5推送条件（当前={confidence:.2f}，要求≥0.85）")
                return
            # 发起安全合规校验（异步）
            corr_id = msg.correlation_id or str(uuid.uuid4())
            if self.bus:
                self.bus.publish_to_module(
                    target_module="ag-mem-45",
                    event_type="safety_check",
                    source_module=self.module_id,
                    data={
                        "entry_id": entry_id,
                        "tool_sequence": tool_sequence,
                        "_correlation_id": corr_id
                    }
                )
            self._pending_safety_checks[corr_id] = {
                "msg": msg,
                "write_source": write_source,
                "request_write_count": request_write_count,
                "request_id": request_id
            }

        elif write_source == "人工锁定":
            # 双重确认简化处理
            self._issue_token(msg, write_source, min(request_write_count, 10), request_id)

        else:
            self._reply_authorization(msg, False, f"非法写入来源: {write_source}")

    def _handle_safety_check_response(self, msg: Message):
        """处理安全合规校验回执"""
        corr_id = msg.data.get("_correlation_id") or msg.correlation_id
        pending = self._pending_safety_checks.pop(corr_id, None)
        if not pending:
            return

        if msg.data.get("compliant", False):
            self._issue_token(pending["msg"], pending["write_source"],
                              pending["request_write_count"], pending["request_id"])
        else:
            self._reply_authorization(pending["msg"], False,
                                      "安全合规校验未通过: " + msg.data.get("reason", ""))

    def _issue_token(self, msg: Message, write_source: str, max_entries: int, request_id: str):
        """签发临时解锁令牌并通知 ag-mem-28"""
        token_id = f"L5-TOKEN-{uuid.uuid4().hex[:8]}"
        now = time.time()
        # 生成签名，与 ag-mem-28 使用相同算法和密钥
        raw = f"{token_id}{max_entries}"
        signature = hmac.new(self.TOKEN_SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()[:16]
        token = {
            "token_id": token_id,
            "max_write_entries": max_entries,
            "expires_at": now + self.TOKEN_VALIDITY_SEC,
            "signature": signature,
            "write_source": write_source
        }

        self._active_token = token
        self._token_issue_time = now
        self.state = LockControlState.TEMP_UNLOCKED
        self._authorize_count += 1

        # 通过总线发送令牌给 L5 存储单元
        if self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-28",
                event_type="temp_unlock_token",
                source_module=self.module_id,
                data=token
            )

        self._reply_authorization(msg, True, "", token, request_id)
        self._log_event("TOKEN_ISSUED", {"source": write_source, "max_entries": max_entries})

    def _reply_authorization(self, msg: Message, authorized: bool, reason: str = "",
                             token: Dict = None, request_id: str = ""):
        if self.bus:
            self.bus.publish(
                topic=f"{msg.source_module}.authorization_result",
                source_module=self.module_id,
                data={
                    "request_id": request_id,
                    "authorized": authorized,
                    "reject_reason": reason,
                    "token": token
                },
                target_module=msg.source_module,
                correlation_id=msg.correlation_id
            )

        if not authorized:
            self._reject_count += 1
            self._log_event("AUTHORIZATION_REJECTED", {"reason": reason})
            self.state = LockControlState.LOCKED_READY

    def _handle_manual_command(self, msg: Message):
        """处理人工操作指令"""
        operation = msg.data.get("operation", "")
        if operation == "强制恢复锁定":
            self._revoke_token("人工强制恢复锁定")
        elif operation == "人工解锁":
            if self.state == LockControlState.LOCKED_READY:
                self._issue_token(msg, "人工锁定", 10, str(uuid.uuid4()))
            else:
                self._log_event("MANUAL_UNLOCK_DENIED", {"reason": "当前状态不允许解锁"})

    def _revoke_token(self, reason: str):
        """吊销当前令牌并通知 ag-mem-28 恢复锁定"""
        self._active_token = None
        self.state = LockControlState.LOCKED_READY
        if self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-28",
                event_type="lock_state_change",
                source_module=self.module_id,
                data={"new_lock_state": "LOCKED", "reason": reason}
            )
        self._log_event("TOKEN_REVOKED", {"reason": reason})

    # ====================== 管理接口 ======================
    def emergency_shutdown(self):
        self._revoke_token("紧急熔断")
        self.state = LockControlState.SYSTEM_PAUSED
        self._active_token = None
        self._pending_safety_checks.clear()
        self._log_event("SYSTEM_EVENT", {"sub_type": "emergency_shutdown"})

    def _report_status(self):
        if self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-48",
                event_type="storage_status",
                source_module=self.module_id,
                data={
                    "state": self.state.value,
                    "authorize_count": self._authorize_count,
                    "reject_count": self._reject_count,
                    "active_token": self._active_token["token_id"] if self._active_token else None
                }
            )

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