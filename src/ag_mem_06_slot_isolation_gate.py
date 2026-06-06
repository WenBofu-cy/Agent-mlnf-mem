#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-06
模块名称: 画像槽数据隔离管控单元
所属分区: 二、漏斗一：用户画像漏斗
核心职责: 在编译期与运行时强制实施漏斗一内部各画像槽之间的物理存储隔离。拦截并校验
          所有跨槽数据访问请求，确保任意时刻任意模块只能访问当前活跃会话绑定的唯一
          画像槽。管理槽级访问令牌的签发、验证与吊销。任何试图跨用户读取或混合画像
          数据的操作均被实时阻断并记录告警。不参与任何认知决策，仅执行数据隔离的强制管控。

依赖模块:
    ag-mem-02(漏斗一专属调度单元), ag-mem-05(画像槽创建与初始化单元),
    ag-mem-07/09/10/11(漏斗一内部模块), ag-mem-51(记忆变更日志追溯单元)
被依赖模块:
    ag-mem-07, ag-mem-09, ag-mem-10, ag-mem-11

安全约束:
  S-01: 编译期硬编码访问权限矩阵，运行时禁止任何模块修改权限配置
  S-02: 所有跨槽访问请求必须被实时阻断，不得以“仅读取不修改”为理由放行
  S-03: 隔离违规事件必须完整记录（来源模块、目标槽位、活跃槽位、请求内容摘要），不可篡改
  S-04: 访问令牌有效期不得超过300秒，过期令牌自动失效
  S-05: 本模块为漏斗一数据隔离的唯一强制执行点，任何模块不得绕过本模块直接访问画像槽存储

版本: V1.0
"""

import hmac
import hashlib
import time
import uuid
from typing import Any, Dict, List, Optional
from enum import Enum

from memory_bus import InternalBus, Message


class GateState(Enum):
    NORMAL_GATE = "normal_gate"
    VIOLATION_BLOCK = "violation_block"
    NO_ACTIVE_SLOT = "no_active_slot"
    SYSTEM_PAUSED = "system_paused"


class OperationType(Enum):
    READ = "读"
    WRITE = "写"
    MODIFY = "修改"
    DELETE = "删除"


# 编译期硬编码访问权限矩阵
ACCESS_PERMISSION_MATRIX: Dict[str, List[OperationType]] = {
    "ag-mem-07": [OperationType.WRITE],
    "ag-mem-09": [OperationType.READ, OperationType.WRITE],
    "ag-mem-10": [OperationType.READ],
    "ag-mem-11": [OperationType.READ],
    "ag-mem-01": [OperationType.READ],
}

# 令牌签名密钥（固定值，实际部署时应与 ag-mem-30 共享并定期轮换）
TOKEN_SIGNING_KEY = b"mlnf-mem-isolation-gate-v1"


class SlotIsolationGate:
    module_id = "ag-mem-06"
    module_name = "画像槽数据隔离管控单元"
    version = "V1.0"

    TOKEN_VALIDITY_SEC = 300
    TOKEN_CLEANUP_INTERVAL_SEC = 60

    def __init__(self):
        self.bus: Optional[InternalBus] = None
        self.state = GateState.NO_ACTIVE_SLOT
        self._active_slot_id: Optional[str] = None
        self._active_user_id: Optional[str] = None
        self._issued_tokens: Dict[str, Dict[str, Any]] = {}
        self._violation_counter: int = 0
        self._last_cleanup_time = time.time()
        self._pending_logs: List[Dict[str, Any]] = []

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    # ====================== 主循环 ======================
    def slot_isolation_gate_main_loop(self):
        if self.state == GateState.SYSTEM_PAUSED:
            return

        if self.bus:
            self.bus.process_batch(10)

        now = time.time()
        if now - self._last_cleanup_time >= self.TOKEN_CLEANUP_INTERVAL_SEC:
            self._cleanup_expired_tokens()
            self._last_cleanup_time = now

    # ====================== 总线消息入口 ======================
    def handle_message(self, msg: Message):
        if not isinstance(msg.data, dict):
            return

        if msg.topic == "ag-mem-06.access_request":
            self._handle_access_request(msg)
            return

        if msg.topic == "ag-mem-06.update_active_slot":
            self._handle_update_active_slot(msg)
            return

        if msg.topic == "ag-mem-06.revoke_tokens":
            self._handle_revoke_tokens(msg)
            return

        if msg.topic == "ag-mem-06.validate_token":
            self._handle_validate_token(msg)
            return

        if msg.topic == "ag-mem-06.isolation_request":
            # 隔离策略申请（来自 ag-mem-05），自动确认
            if self.bus:
                self.bus.publish_to_module(
                    target_module=msg.source_module,
                    event_type="isolation_config",
                    source_module=self.module_id,
                    data={
                        "slot_id": msg.data.get("slot_id"),
                        "success": True,
                        "encryption_key": msg.data.get("encryption_key"),
                        "_correlation_id": msg.data.get("_correlation_id") or msg.correlation_id
                    }
                )
            return

    def _handle_access_request(self, msg: Message):
        """处理数据访问请求"""
        data = msg.data
        source = data.get("source_module", msg.source_module)
        operation_str = data.get("operation", OperationType.READ.value)  # 中文值：读/写/修改/删除
        target_slot = data.get("target_slot_id", "")

        # 检查活跃槽位
        if self.state == GateState.NO_ACTIVE_SLOT or self._active_slot_id is None:
            self._reply_deny(msg, "无活跃画像槽")
            return

        # 检查权限
        if source not in ACCESS_PERMISSION_MATRIX:
            self._report_violation(msg, "未授权模块", source, target_slot)
            self._reply_deny(msg, "未授权模块")
            return

        # 跨槽检测
        if target_slot != self._active_slot_id:
            self._report_violation(msg, "跨槽访问", source, target_slot)
            self._reply_deny(msg, "跨槽访问被禁止")
            return

        # 操作类型校验
        try:
            op = OperationType(operation_str)
        except ValueError:
            self._reply_deny(msg, "无效操作类型")
            return
        if op not in ACCESS_PERMISSION_MATRIX[source]:
            self._reply_deny(msg, "操作类型越权")
            return

        # 签发令牌（含签名）
        token = self._issue_token(source, op, target_slot)
        if self.bus:
            self.bus.publish(
                topic=f"{msg.source_module}.access_token",
                source_module=self.module_id,
                data={
                    "token_id": token["token_id"],
                    "authorized_slot_id": token["authorized_slot_id"],
                    "authorized_operation": token["authorized_operation"].value,
                    "expires_at": token["expires_at"],
                    "signature": token["signature"],
                },
                target_module=msg.source_module,
                correlation_id=msg.correlation_id
            )

    def _handle_update_active_slot(self, msg: Message):
        """更新当前活跃槽位绑定"""
        data = msg.data
        new_slot = data.get("slot_id", "")
        new_user = data.get("user_id", "")
        if new_slot != self._active_slot_id:
            self._revoke_all_for_slot(self._active_slot_id)
        self._active_slot_id = new_slot
        self._active_user_id = new_user
        self.state = GateState.NORMAL_GATE if new_slot else GateState.NO_ACTIVE_SLOT

    def _handle_revoke_tokens(self, msg: Message):
        """吊销指定槽位所有令牌"""
        slot_id = msg.data.get("slot_id", "")
        count = self._revoke_all_for_slot(slot_id)
        if self.bus:
            self.bus.publish(
                topic=f"{msg.source_module}.revoke_confirm",
                source_module=self.module_id,
                data={"slot_id": slot_id, "revoked_count": count},
                target_module=msg.source_module,
                correlation_id=msg.correlation_id
            )

    def _handle_validate_token(self, msg: Message):
        """验证令牌有效性"""
        token_id = msg.data.get("token_id", "")
        token = self._issued_tokens.get(token_id)
        valid = False
        if token and token["expires_at"] > time.time():
            # 可选：验证签名
            valid = True
        if self.bus:
            self.bus.publish(
                topic=f"{msg.source_module}.token_validation",
                source_module=self.module_id,
                data={"token_id": token_id, "valid": valid},
                target_module=msg.source_module,
                correlation_id=msg.correlation_id
            )

    # ====================== 内部辅助 ======================
    def _issue_token(self, source: str, operation: OperationType, slot_id: str) -> Dict[str, Any]:
        token_id = f"TOKEN-{uuid.uuid4().hex[:8]}"
        now = time.time()
        expires = now + self.TOKEN_VALIDITY_SEC
        signature = self._generate_signature(token_id, slot_id, operation.value, now)
        token = {
            "token_id": token_id,
            "authorized_slot_id": slot_id,
            "authorized_operation": operation,
            "issued_at": now,
            "expires_at": expires,
            "source_module": source,
            "signature": signature,
        }
        self._issued_tokens[token_id] = token
        return token

    @staticmethod
    def _generate_signature(token_id: str, slot_id: str, operation: str, timestamp: float) -> str:
        """生成 HMAC-SHA256 签名"""
        message = f"{token_id}|{slot_id}|{operation}|{timestamp}".encode('utf-8')
        return hmac.new(TOKEN_SIGNING_KEY, message, hashlib.sha256).hexdigest()

    def _revoke_all_for_slot(self, slot_id: str) -> int:
        if not slot_id:
            return 0
        count = 0
        for tid, t in list(self._issued_tokens.items()):
            if t["authorized_slot_id"] == slot_id:
                del self._issued_tokens[tid]
                count += 1
        return count

    def _cleanup_expired_tokens(self):
        now = time.time()
        for tid, t in list(self._issued_tokens.items()):
            if t["expires_at"] <= now:
                del self._issued_tokens[tid]

    def _reply_deny(self, msg: Message, reason: str):
        if self.bus:
            self.bus.publish(
                topic=f"{msg.source_module}.access_denied",
                source_module=self.module_id,
                data={"deny_reason": reason},
                target_module=msg.source_module,
                correlation_id=msg.correlation_id
            )

    def _report_violation(self, msg: Message, vtype: str, source: str, target: str):
        """记录隔离违规事件（完整字段）"""
        self._violation_counter += 1
        summary = str(msg.data.get("summary", ""))[:100] if msg.data else ""
        self._log_event("VIOLATION", {
            "type": vtype,
            "source_module": source,
            "target_slot": target,
            "active_slot": self._active_slot_id,
            "request_summary": summary,
        })

    # ====================== 熔断与日志 ======================
    def emergency_shutdown(self):
        self.state = GateState.SYSTEM_PAUSED
        self._issued_tokens.clear()
        self._active_slot_id = None
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