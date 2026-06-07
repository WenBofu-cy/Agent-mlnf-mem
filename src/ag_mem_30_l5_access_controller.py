#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-30
模块名称: L5核心层防篡改与只读管控单元
所属分区: 三、漏斗二：任务经验漏斗 / 五层存储
核心职责: 作为L5核心层数据的唯一对外访问接口，管控所有外部模块对L5层经验的读写权限。
          对外部模块仅提供经过令牌验证的只读查询服务，任何写入请求必须携带由ag-mem-29
          签发的有效写入安全令牌。管理查询令牌的签发、验证与吊销，确保L5数据不被未授权
          模块访问或修改。不参与经验内容的管理决策，仅执行访问权限的强制管控。

依赖模块:
    ag-mem-28(L5核心层存储单元), ag-mem-29(L5核心层安全规则硬锁定单元)
被依赖模块:
    ag-mem-15~19(各场景分槽), ag-mem-28

安全约束:
  S-01: L5层数据的所有访问必须经过本模块的令牌验证，禁止任何模块绕过本模块直接访问L5存储
  S-02: 查询令牌仅授权只读操作，任何携带查询令牌的写入请求一律拒绝
  S-03: 写入令牌必须与ag-mem-29签发的活跃令牌完全一致（ID+签名双重校验），任何不匹配视为令牌伪造
  S-04: 令牌签名采用HMAC-SHA256，密钥仅本模块与ag-mem-29共享，外部模块无法伪造
  S-05: 违规访问尝试必须实时阻断并完整记录告警日志，不可篡改
  S-06: 查询令牌过期后自动失效，不得通过任何方式延长有效期

版本: V1.0
"""

import time
import uuid
import hmac
import hashlib
from typing import Any, Dict, List, Optional, Set
from enum import Enum

from memory_bus import InternalBus, Message


class AccessState(Enum):
    NORMAL_GATE = "normal_gate"
    VIOLATION_BLOCK = "violation_block"
    SYSTEM_PAUSED = "system_paused"


QUERY_AUTHORIZED_MODULES: Set[str] = {
    "ag-mem-15", "ag-mem-16", "ag-mem-17", "ag-mem-18", "ag-mem-19"
}


class L5AccessController:
    module_id = "ag-mem-30"
    module_name = "L5核心层防篡改与只读管控单元"
    version = "V1.0"

    QUERY_TOKEN_VALIDITY_SEC = 300
    MAX_ACTIVE_QUERY_TOKENS = 20
    CLEANUP_INTERVAL_SEC = 60
    STATUS_REPORT_INTERVAL_SEC = 120

    def __init__(self):
        self.bus: Optional[InternalBus] = None
        self.state = AccessState.NORMAL_GATE

        self._active_query_tokens: Dict[str, Dict[str, Any]] = {}
        self._active_write_token: Optional[Dict[str, Any]] = None

        self._issued_today: int = 0
        self._rejected_today: int = 0
        self._violation_count: int = 0
        self._last_cleanup_time: float = time.time()
        self._last_status_time: float = time.time()
        self._pending_logs: List[Dict[str, Any]] = []

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    # ====================== 统一主循环入口 ======================
    def run_cycle(self):
        self.l5_access_control_main_loop()

    def l5_access_control_main_loop(self):
        if self.state == AccessState.SYSTEM_PAUSED:
            return

        if self.bus:
            self.bus.process_batch(10)

        now = time.time()

        if now - self._last_cleanup_time >= self.CLEANUP_INTERVAL_SEC:
            self._cleanup_expired_tokens(now)
            self._last_cleanup_time = now

        if now - self._last_status_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._report_status()
            self._last_status_time = now

    # ====================== 总线消息入口 ======================
    def handle_message(self, msg: Message):
        if not isinstance(msg.data, dict):
            return

        if msg.topic == "ag-mem-30.query_token_request":
            self._handle_query_token_request(msg)
            return

        if msg.topic == "ag-mem-30.validate_token":
            self._handle_token_validation(msg)
            return

        if msg.topic == "ag-mem-30.validate_write_token":
            self._handle_write_token_validation(msg)
            return

        if msg.topic == "ag-mem-30.sync_write_token":
            self._handle_write_token_sync(msg)
            return

        if msg.topic == "ag-mem-30.revoke_write_token":
            self._active_write_token = None
            self._log_event("WRITE_TOKEN_REVOKED", {"reason": "来自ag-mem-29吊销指令"})
            return

    def _handle_query_token_request(self, msg: Message):
        """签发查询令牌（仅对授权模块开放）"""
        source_module = msg.data.get("source_module_id", msg.source_module)
        source_slot = msg.data.get("source_slot_id", "")

        if source_module not in QUERY_AUTHORIZED_MODULES:
            self._reject_and_alert("查询", f"未授权模块: {source_module}", "严重",
                                   source_module=source_module)
            self._reply_token_issue(msg, False, error="未授权模块")
            return

        if len(self._active_query_tokens) >= self.MAX_ACTIVE_QUERY_TOKENS:
            oldest_id = min(self._active_query_tokens.keys(),
                            key=lambda tid: self._active_query_tokens[tid]["issued_at"])
            del self._active_query_tokens[oldest_id]
            self._log_event("QUERY_TOKEN_EVICTED", {"token_id": oldest_id})

        token_id = f"L5-QTOKEN-{uuid.uuid4().hex[:8]}"
        now = time.time()
        signature = self._generate_signature(token_id)

        token = {
            "token_id": token_id,
            "authorized_slot_id": source_slot,
            "authorized_module_id": source_module,
            "issued_at": now,
            "expires_at": now + self.QUERY_TOKEN_VALIDITY_SEC,
            "signature": signature
        }
        self._active_query_tokens[token_id] = token
        self._issued_today += 1

        self._log_event("QUERY_TOKEN_ISSUED", {
            "token_id": token_id,
            "source_module": source_module,
            "source_slot": source_slot
        })

        self._reply_token_issue(msg, True, token_id=token_id, slot_id=source_slot)

    def _handle_token_validation(self, msg: Message):
        """验证查询令牌有效性（供 ag-mem-28 调用）——不要求外部回传签名"""
        token_id = msg.data.get("token", "")
        token = self._active_query_tokens.get(token_id)

        valid = False
        authorized_slot = ""
        reason = ""

        if not token:
            reason = "令牌不存在"
        elif time.time() > token["expires_at"]:
            del self._active_query_tokens[token_id]
            reason = "令牌已过期"
        else:
            valid = True
            authorized_slot = token["authorized_slot_id"]

        if self.bus:
            self.bus.publish(
                topic="ag-mem-28.token_validation_result",
                source_module=self.module_id,
                data={
                    "valid": valid,
                    "authorized_slot_id": authorized_slot,
                    "reason": reason if not valid else "",
                    "_correlation_id": msg.data.get("_correlation_id", "")
                },
                target_module="ag-mem-28",
                correlation_id=msg.correlation_id
            )

        if not valid:
            self._rejected_today += 1

    def _handle_write_token_validation(self, msg: Message):
        """写入令牌验证（S-02 / S-03 / S-04 强制校验）"""
        token_data = msg.data.get("write_token", {})
        token_id = token_data.get("token_id", "")
        request_source = msg.source_module

        valid = False
        reason = ""

        if not self._active_write_token:
            reason = "无活跃写入令牌"
        elif self._active_write_token.get("token_id") != token_id:
            reason = "写入令牌ID不匹配"
        elif not self._verify_write_token_signature(token_data):
            reason = "写入令牌签名伪造"
        elif token_id in self._active_query_tokens:
            reason = "查询令牌禁止用于写入操作"
            self._violation_count += 1
        else:
            valid = True

        if self.bus:
            self.bus.publish(
                topic="ag-mem-28.write_token_validation_result",
                source_module=self.module_id,
                data={
                    "valid": valid,
                    "reason": reason,
                    "_correlation_id": msg.data.get("_correlation_id", "")
                },
                target_module="ag-mem-28",
                correlation_id=msg.correlation_id
            )

        if not valid:
            self._rejected_today += 1
            self._reject_and_alert("写入", reason,
                                   "严重" if "伪造" in reason or "禁止" in reason else "警告",
                                   source_module=request_source)

    def _handle_write_token_sync(self, msg: Message):
        """接收并校验 ag-mem-29 写入令牌（S-03/S-04）"""
        if msg.source_module != "ag-mem-29":
            self._reject_and_alert("写入令牌同步", "非法来源模块，仅ag-mem-29可同步", "严重",
                                   source_module=msg.source_module)
            return

        token = msg.data
        if not token or not self._verify_write_token_signature(token):
            self._reject_and_alert("写入令牌同步", "写入令牌签名校验失败", "严重")
            return

        self._active_write_token = token
        self._log_event("WRITE_TOKEN_SYNCED", {"token_id": token.get("token_id")})

    def _generate_signature(self, token_id: str) -> str:
        return hmac.new(
            self._secret_key().encode(),
            token_id.encode(),
            hashlib.sha256
        ).hexdigest()[:32]

    def _verify_write_token_signature(self, token: Dict[str, Any]) -> bool:
        token_id = token.get("token_id", "")
        expected_sig = token.get("signature", "")
        if not token_id or not expected_sig:
            return False
        return hmac.compare_digest(self._generate_signature(token_id), expected_sig)

    def _reply_token_issue(self, msg: Message, success: bool, token_id: str = "",
                           slot_id: str = "", error: str = ""):
        if not self.bus:
            return
        self.bus.publish(
            topic=f"{msg.source_module}.query_token_response",
            source_module=self.module_id,
            data={
                "success": success,
                "token_id": token_id,
                "authorized_slot_id": slot_id,
                "expires_in_seconds": self.QUERY_TOKEN_VALIDITY_SEC if success else 0,
                "error": error
            },
            target_module=msg.source_module,
            correlation_id=msg.correlation_id
        )

    def _reject_and_alert(self, operation: str, reason: str, severity: str,
                          source_module: str = ""):
        self._rejected_today += 1
        if severity == "严重":
            self._violation_count += 1

        self._log_event(f"{operation}_REJECTED", {
            "reason": reason,
            "severity": severity,
            "source_module": source_module
        })

    def _cleanup_expired_tokens(self, now: float):
        expired = [tid for tid, t in self._active_query_tokens.items()
                   if now > t["expires_at"]]
        for tid in expired:
            del self._active_query_tokens[tid]
        if expired:
            self._log_event("QUERY_TOKENS_EXPIRED", {"count": len(expired)})

    def _report_status(self):
        if self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-03",
                event_type="internal_status",
                source_module=self.module_id,
                data={
                    "state": self.state.value,
                    "issued_today": self._issued_today,
                    "rejected_today": self._rejected_today,
                    "active_query_tokens": len(self._active_query_tokens),
                    "active_write_token": self._active_write_token is not None,
                    "violation_count": self._violation_count
                }
            )

    @staticmethod
    def _secret_key() -> str:
        return "l5-access-control-secret-key-2025-v1"

    # ====================== 管理接口 ======================
    def emergency_shutdown(self):
        self.state = AccessState.SYSTEM_PAUSED
        self._active_query_tokens.clear()
        self._active_write_token = None
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