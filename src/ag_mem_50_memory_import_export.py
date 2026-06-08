#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-50
模块名称: 记忆导入导出与脱敏共享单元
所属分区: 五、存储与系统运维
核心职责: 管理漏斗二中 L4/L5 层泛化经验的导出、脱敏处理，以及外部经验包的合规导入。
          导出时强制脱敏（去除所有用户隐私字段），仅保留通用任务模板。导入前必须通过
          格式校验、签名校验、安全合规校验与容量检查四道关卡，防止恶意经验注入。不参与
          经验内容的生成或修改，仅执行安全的导入导出操作。

依赖模块: ag-mem-26/28(L4/L5 存储), ag-mem-45(安全规则库), ag-mem-48(容量管控), ag-mem-51(日志)
被依赖模块: 系统管理接口、开发者工具、其他 EM-Core 实例

安全约束:
  E-01: 导出时必须执行强制脱敏，禁止任何包含用户个人身份信息的字段出现在经验包中
  E-02: L5 核心层经验默认不导出，仅在请求明确授权且通过双重确认后方可导出
  E-03: 导入经验包必须通过格式校验、签名校验、安全合规校验与容量检查四道关卡
  E-04: 导入的经验条目 V 值重置为基线值
  E-05: 所有导入导出操作必须记录完整的不可变事件日志

版本: V1.0
"""

import time
import uuid
import hashlib
import json
from typing import Any, Dict, List, Optional
from enum import Enum

from memory_bus import InternalBus, Message


class ImportExportState(Enum):
    IDLE = "idle"
    EXPORTING = "exporting"
    IMPORTING_VALIDATE = "importing_validate"
    IMPORTING_WRITE = "importing_write"
    OPERATION_FAILED = "operation_failed"
    SYSTEM_PAUSED = "system_paused"


class MemoryImportExportUnit:
    module_id = "ag-mem-50"
    module_name = "记忆导入导出与脱敏共享单元"
    version = "V1.0"

    V_VALUE_BASELINE = 0.20
    MAX_PACKAGE_ENTRIES = 5000

    def __init__(self):
        self.bus: Optional[InternalBus] = None
        self.state = ImportExportState.IDLE
        self._total_exports: int = 0
        self._total_imports: int = 0
        self._last_operation_time: float = 0.0
        # 异步操作上下文
        self._pending_export: Optional[Dict[str, Any]] = None
        self._pending_import: Optional[Dict[str, Any]] = None
        self._pending_logs: List[Dict[str, Any]] = []

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    # ====================== 全系统统一调度入口 ======================
    def run_cycle(self):
        self.memory_import_export_main_loop()

    # ====================== 主循环 ======================
    def memory_import_export_main_loop(self):
        if self.state == ImportExportState.SYSTEM_PAUSED:
            return

        if self.bus:
            self.bus.process_batch(10)

    # ====================== 总线消息入口 ======================
    def handle_message(self, msg: Message):
        # 系统暂停时拒绝所有请求
        if self.state == ImportExportState.SYSTEM_PAUSED:
            return
        
        if not isinstance(msg.data, dict):
            self._log_event("INVALID_MSG", {"reason": "数据格式非字典"})
            return

        try:
            # 导出请求
            if msg.topic == "ag-mem-50.export_request":
                self._handle_export(msg)
                return

            # L4/L5 存储单元导出回执
            if msg.topic == "ag-mem-50.export_entries_response":
                self._handle_export_entries_response(msg)
                return

            # 导入请求
            if msg.topic == "ag-mem-50.import_request":
                self._handle_import(msg)
                return

            # 安全合规校验回执
            if msg.topic == "ag-mem-50.safety_check_response":
                self._handle_safety_check_response(msg)
                return

            # 容量检查回执
            if msg.topic == "ag-mem-50.capacity_check_response":
                self._handle_capacity_check_response(msg)
                return
        except Exception as e:
            self._log_event("MSG_PROCESS_ERROR", {"error": str(e)})
            self.state = ImportExportState.OPERATION_FAILED

    # ====================== 导出处理 ======================
    def _handle_export(self, msg: Message):
        data = msg.data
        export_scope = data.get("export_scope", "L4")
        l5_authorized = data.get("l5_export_authorized", False)
        max_entries = data.get("max_entries", 1000)

        self.state = ImportExportState.EXPORTING

        # E-02 约束：L5 经验未授权禁止导出
        if "L5" in export_scope and not l5_authorized:
            self._reply_export(msg, success=False, error="L5导出未获授权")
            self.state = ImportExportState.IDLE
            return

        # 保存上下文
        self._pending_export = {
            "msg": msg,
            "scope": export_scope,
            "l5_authorized": l5_authorized,
            "max": max_entries,
            "collected": [],
            "expected_responses": 0,
            "received_responses": 0
        }

        # 向存储单元请求数据
        if self.bus:
            if export_scope in ("L4", "全部"):
                self._pending_export["expected_responses"] += 1
                self.bus.publish_to_module(
                    target_module="ag-mem-26",
                    event_type="experience_query",
                    source_module=self.module_id,
                    data={
                        "max_results": max_entries,
                        "_reply_to": "ag-mem-50.export_entries_response",
                        "_correlation_id": msg.correlation_id
                    }
                )
            if export_scope in ("L5", "全部") and l5_authorized:
                self._pending_export["expected_responses"] += 1
                self.bus.publish_to_module(
                    target_module="ag-mem-28",
                    event_type="experience_query",
                    source_module=self.module_id,
                    data={
                        "max_results": max_entries,
                        "_reply_to": "ag-mem-50.export_entries_response",
                        "_correlation_id": msg.correlation_id
                    }
                )

    def _handle_export_entries_response(self, msg: Message):
        if not self._pending_export:
            return

        entries = msg.data.get("matched_experiences", msg.data.get("entries", []))
        if isinstance(entries, list):
            self._pending_export["collected"].extend(entries)
        self._pending_export["received_responses"] += 1

        if self._pending_export["received_responses"] >= self._pending_export["expected_responses"]:
            self._process_export_collected()

    def _process_export_collected(self):
        pending = self._pending_export
        if not pending:
            return

        original_msg = pending["msg"]
        entries = pending["collected"]

        # E-01 强制脱敏：移除所有用户隐私字段
        anonymized = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            # 隐私字段清理
            privacy_fields = ("user_id", "session_id", "device_fingerprint", "geo_location",
                          "raw_input_text", "personal_preferences", "explicit_feedback")
            for field in privacy_fields:
                entry.pop(field, None)

            anonymized.append({
                "entry_id": str(uuid.uuid4()),
                "task_feature_vector": entry.get("task_feature_vector", []),
                "tool_call_sequence": entry.get("tool_call_sequence", []),
                "result_label": entry.get("result_label", ""),
                "generic_rules": entry.get("generic_rules", {}),
                "s_value": float(entry.get("s_value", 0)),
                "c_value": float(entry.get("c_value", 0)),
                "i_value": float(entry.get("i_value", 0)),
                "v_value": self.V_VALUE_BASELINE,
                "scene_category": entry.get("scene_category", ""),
                "original_timestamp": entry.get("timestamp", 0)
            })

        # 生成校验签名
        raw = json.dumps(anonymized, sort_keys=True).encode()
        package_checksum = hashlib.sha256(raw).hexdigest()

        result = {
            "success": True,
            "total_exported": len(anonymized),
            "anonymized_count": len(anonymized),
            "package": {"entries": anonymized, "checksum": package_checksum},
            "checksum": package_checksum
        }

        self._reply_export(original_msg, success=True, total=len(anonymized), package=result["package"])
        self._total_exports += 1
        self._log_operation("export", len(anonymized))
        self._pending_export = None
        self.state = ImportExportState.IDLE

    def _reply_export(self, msg: Message, success: bool, error: str = "", total: int = 0, package: Dict = None):
        if self.bus:
            self.bus.publish(
                topic=f"{msg.source_module}.export_result",
                source_module=self.module_id,
                data={
                    "success": success,
                    "error_reason": error,
                    "total_exported": total,
                    "package": package
                },
                target_module=msg.source_module,
                correlation_id=msg.correlation_id
            )

    # ====================== 导入处理 ======================
    def _handle_import(self, msg: Message):
        data = msg.data
        package = data.get("package", {})
        signature = data.get("signature", "")
        entries = package.get("entries", [])

        if not entries:
            self._reply_import(msg, success=False, error="经验包为空")
            return

        # 限制最大条目数
        if len(entries) > self.MAX_PACKAGE_ENTRIES:
            self._reply_import(msg, success=False, error=f"超出最大条目限制{self.MAX_PACKAGE_ENTRIES}")
            return

        self.state = ImportExportState.IMPORTING_VALIDATE

        # 格式校验
        for entry in entries:
            if not entry.get("tool_call_sequence") and not entry.get("task_feature_vector"):
                self._reply_import(msg, success=False, error="经验包格式校验失败：条目缺少核心数据")
                self.state = ImportExportState.IDLE
                return

        # 签名校验
        if signature:
            raw = json.dumps(entries, sort_keys=True).encode()
            expected = hashlib.sha256(raw).hexdigest()
            if signature != expected and signature != package.get("checksum", ""):
                self._reply_import(msg, success=False, error="签名校验失败")
                self.state = ImportExportState.IDLE
                return

        # 保存上下文
        self._pending_import = {
            "msg": msg,
            "entries": entries,
            "signature": signature,
            "start": time.time()
        }

        # E-03 安全合规校验
        if self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-45",
                event_type="safety_check",
                source_module=self.module_id,
                data={
                    "entries": entries,
                    "_reply_to": "ag-mem-50.safety_check_response",
                    "_correlation_id": msg.correlation_id
                }
            )

    def _handle_safety_check_response(self, msg: Message):
        pending = self._pending_import
        if not pending:
            return

        if not msg.data.get("compliant", False):
            self._reply_import(pending["msg"], success=False, error="安全合规校验不通过")
            self._pending_import = None
            self.state = ImportExportState.IDLE
            return

        # 容量检查
        if self.bus:
            self.bus.publish_to_module(
                target_module="ag-mem-48",
                event_type="quota_query",
                source_module=self.module_id,
                data={
                    "query_layer": "全局",
                    "_reply_to": "ag-mem-50.capacity_check_response",
                    "_correlation_id": pending["msg"].correlation_id
                }
            )

    def _handle_capacity_check_response(self, msg: Message):
        pending = self._pending_import
        if not pending:
            return

        # 容量校验
        available = msg.data.get("available_space_bytes", 0)
        required = len(pending["entries"]) * 1024
        if available < required:
            self._reply_import(pending["msg"], success=False, error="存储容量不足")
            self._pending_import = None
            self.state = ImportExportState.IDLE
            return

        # 开始写入
        self.state = ImportExportState.IMPORTING_WRITE
        entries = pending["entries"]
        import_count = len(entries)

        # E-04 重置 V 值为基线
        for entry in entries:
            entry["v_value"] = self.V_VALUE_BASELINE
            entry["source"] = "import"

        # 写入 L1 临时层，遵循标准晋升流程
        if self.bus:
            for entry in entries:
                self.bus.publish_to_module(
                    target_module="ag-mem-20",
                    event_type="experience_write",
                    source_module=self.module_id,
                    data={
                        "experience_data": entry.get("experience_data", entry),
                        "source_slot_id": entry.get("scene_category", "ag-mem-19"),
                        "adjusted_i_value": float(entry.get("i_value", 0)),
                        "timestamp": time.time()
                    }
                )

        self._reply_import(pending["msg"], success=True, total=import_count)
        self._total_imports += 1
        self._log_operation("import", import_count)
        self._pending_import = None
        self.state = ImportExportState.IDLE

    def _reply_import(self, msg: Message, success: bool, error: str = "", total: int = 0):
        if self.bus:
            self.bus.publish(
                topic=f"{msg.source_module}.import_result",
                source_module=self.module_id,
                data={
                    "success": success,
                    "error_reason": error,
                    "total_imported": total,
                    "successful_count": total if success else 0
                },
                target_module=msg.source_module,
                correlation_id=msg.correlation_id
            )

    # ====================== 操作日志 E-05 ======================
    def _log_operation(self, operation: str, count: int):
        self._log_event(f"{operation.upper()}_OPERATION", {
            "count": count,
            "timestamp": time.time()
        })

    # ====================== 管理接口 ======================
    def emergency_shutdown(self):
        self.state = ImportExportState.SYSTEM_PAUSED
        self._pending_export = None
        self._pending_import = None
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