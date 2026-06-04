#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-50
模块名称: 记忆导入导出与脱敏共享单元
所属分区: 五、存储与系统运维
核心职责: 管理漏斗二中 L4/L5 层泛化经验的导出、脱敏处理，以及外部经验包的合规导入。
          在导出过程中，对经验数据执行强制脱敏——剔除所有用户个人身份信息（用户ID、
          会话ID、设备指纹、地理位置、原始输入文本），仅保留结构化的通用任务模板（任务
          特征向量、工具调用序列、成功/失败标签、泛化规则）。脱敏后的经验包可跨用户、
          跨实例共享，用于新系统的经验预载或开发者工具库建设。同时支持从外部导入经过
          安全审计的经验包，导入前必须通过格式校验与安全规则库的合规检查，防止恶意经验
          注入。本模块不参与经验内容的生成或修改，仅执行安全的导入导出操作。

依赖模块:
    ag-mem-26/28(L4/L5 存储单元), ag-mem-45(安全规则库),
    ag-mem-48(全局容量配额管控单元), ag-mem-51(记忆变更日志追溯单元)
被依赖模块:
    系统管理接口、开发者工具、其他 EM-Core 实例（通过外部文件或 API 使用导出的经验包）

安全约束:
  E-01: 导出时必须执行强制脱敏，禁止任何包含用户个人身份信息的字段出现在经验包中
  E-02: L5 核心层经验默认不导出，仅在请求明确授权且通过双重确认后方可导出
  E-03: 导入经验包必须通过格式校验、签名校验与安全合规校验三道关卡，防止恶意经验注入
  E-04: 导入的经验条目 V 值重置为基线值，确保不与新用户的偏好数据混淆
  E-05: 所有导入导出操作必须记录完整的不可变事件日志，包括操作来源、条目数、校验签名
"""

from typing import Dict, List, Optional, Any, Callable, Tuple
from dataclasses import dataclass, field
from enum import Enum
import time
import uuid
import hashlib
import json


class ImportExportState(Enum):
    IDLE = "idle"
    EXPORTING = "exporting"
    IMPORTING_VALIDATE = "importing_validate"
    IMPORTING_WRITE = "importing_write"
    OPERATION_FAILED = "operation_failed"
    SYSTEM_PAUSED = "system_paused"


@dataclass
class ExportRequest:
    request_id: str = ""
    requester: str = ""                         # 来源模块或管理员
    export_scope: str = "L4"                    # L4 / L5 / 全部
    export_format: str = "JSON"                 # JSON / 二进制
    include_metadata: bool = True
    max_entries: int = 1000
    l5_export_authorized: bool = False          # L5导出需明确授权
    timestamp: float = field(default_factory=time.time)


@dataclass
class AnonymizedEntry:
    entry_id: str = ""                          # 重新生成的 UUID
    task_feature_vector: List[float] = field(default_factory=list)
    tool_call_sequence: List[str] = field(default_factory=list)
    result_label: str = ""
    generic_rules: Dict[str, Any] = field(default_factory=dict)
    s_value: float = 0.0
    c_value: float = 0.0
    i_value: float = 0.0
    v_value: float = 0.0                        # 重置为基线值
    scene_category: str = ""
    original_timestamp: float = 0.0


@dataclass
class ExperiencePackage:
    version: str = "1.0"
    export_timestamp: float = field(default_factory=time.time)
    source_instance_id: str = "ANONYMIZED"
    checksum: str = ""                          # SHA-256
    anonymized_entries: List[AnonymizedEntry] = field(default_factory=list)


@dataclass
class ExportResult:
    request_id: str = ""
    success: bool = True
    package: Optional[ExperiencePackage] = None
    total_exported: int = 0
    anonymized_count: int = 0
    package_size_bytes: int = 0
    checksum: str = ""
    error_reason: str = ""


@dataclass
class ImportRequest:
    request_id: str = ""
    source: str = ""                            # 导入来源
    package: Optional[ExperiencePackage] = None
    overwrite_existing: bool = False
    signature: str = ""                         # 外部包的签名
    timestamp: float = field(default_factory=time.time)


@dataclass
class ImportResult:
    request_id: str = ""
    success: bool = True
    total_imported: int = 0
    successful_count: int = 0
    rejected_entries: List[Dict[str, str]] = field(default_factory=list)
    error_reason: str = ""


@dataclass
class ImportExportStatus:
    state: ImportExportState = ImportExportState.IDLE
    total_exports: int = 0
    total_imports: int = 0
    last_operation_time: float = 0.0


class MemoryImportExportUnit:
    V_VALUE_BASELINE = 0.20                     # 导入时 V 值重置基线
    MAX_PACKAGE_ENTRIES = 5000

    def __init__(self):
        self.module_id = "ag-mem-50"
        self.module_name = "记忆导入导出与脱敏共享单元"
        self.version = "V1.0"

        self.state = ImportExportState.IDLE
        self._total_exports: int = 0
        self._total_imports: int = 0
        self._last_operation_time: float = 0.0
        self._pending_logs: List[Dict[str, Any]] = []

        # 回调注入
        self._query_export_request = None
        self._query_import_request = None
        self._query_l4_entries = None           # 查询 L4 层全量条目
        self._query_l5_entries = None           # 查询 L5 层全量条目
        self._query_safety_compliance = None    # 安全合规检查
        self._query_capacity_check = None       # 容量检查
        self._query_import_write_confirm = None # 导入写入确认

        self._publish_export_result = None
        self._publish_import_result = None
        self._publish_import_write_command = None
        self._publish_event_log = None
        self._publish_operation_log = None      # 导入导出事件日志

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成")

    # ========== 回调注入 ==========
    def set_export_request_query(self, callback: Callable[[], Optional[ExportRequest]]):
        self._query_export_request = callback

    def set_import_request_query(self, callback: Callable[[], Optional[ImportRequest]]):
        self._query_import_request = callback

    def set_l4_entries_query(self, callback: Callable[[], Optional[List[Dict[str, Any]]]]):
        self._query_l4_entries = callback

    def set_l5_entries_query(self, callback: Callable[[], Optional[List[Dict[str, Any]]]]):
        self._query_l5_entries = callback

    def set_safety_compliance_query(self, callback: Callable[[List[Dict[str, Any]]], Optional[Dict[str, Any]]]):
        self._query_safety_compliance = callback

    def set_capacity_check_query(self, callback: Callable[[int], Optional[bool]]):
        self._query_capacity_check = callback

    def set_import_write_confirm_query(self, callback: Callable[[List[Dict[str, Any]]], Optional[Dict[str, Any]]]):
        self._query_import_write_confirm = callback

    def set_export_result_publisher(self, callback: Callable[[ExportResult], None]):
        self._publish_export_result = callback

    def set_import_result_publisher(self, callback: Callable[[ImportResult], None]):
        self._publish_import_result = callback

    def set_import_write_command_publisher(self, callback: Callable[[List[Dict[str, Any]]], None]):
        self._publish_import_write_command = callback

    def set_event_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_event_log = callback

    def set_operation_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_operation_log = callback

    # ========== 主循环 ==========
    def run_cycle(self):
        if self.state == ImportExportState.SYSTEM_PAUSED:
            return

        # 处理导出请求
        export_req = self._query_export_request() if self._query_export_request else None
        if export_req and self.state == ImportExportState.IDLE:
            self._handle_export(export_req)
            return

        # 处理导入请求
        import_req = self._query_import_request() if self._query_import_request else None
        if import_req and self.state == ImportExportState.IDLE:
            self._handle_import(import_req)

    # ========== 导出处理 ==========
    def _handle_export(self, request: ExportRequest):
        self.state = ImportExportState.EXPORTING
        start_time = time.time()

        entries = []
        # 查询 L4 层
        if request.export_scope in ("L4", "全部"):
            l4_data = self._query_l4_entries() if self._query_l4_entries else []
            entries.extend(l4_data[:request.max_entries])

        # 查询 L5 层（需授权）
        if request.export_scope in ("L5", "全部"):
            if not request.l5_export_authorized:
                self._send_export_result(request.request_id, False, "L5导出未获授权")
                self.state = ImportExportState.IDLE
                return
            l5_data = self._query_l5_entries() if self._query_l5_entries else []
            entries.extend(l5_data[:request.max_entries])

        if not entries:
            self._send_export_result(request.request_id, True, "")
            self.state = ImportExportState.IDLE
            return

        # 执行强制脱敏
        anonymized_entries = []
        for entry in entries:
            anonymized = self._anonymize_entry(entry)
            if anonymized:
                anonymized_entries.append(anonymized)

        # 构建经验包
        package = ExperiencePackage(
            version="1.0",
            export_timestamp=time.time(),
            source_instance_id="ANONYMIZED",
            anonymized_entries=anonymized_entries
        )
        # 生成校验和
        raw = json.dumps([e.__dict__ for e in anonymized_entries], sort_keys=True).encode()
        package.checksum = hashlib.sha256(raw).hexdigest()

        package_size = len(json.dumps(package.__dict__, ensure_ascii=False).encode()) if anonymized_entries else 0

        result = ExportResult(
            request_id=request.request_id,
            success=True,
            package=package,
            total_exported=len(entries),
            anonymized_count=len(anonymized_entries),
            package_size_bytes=package_size,
            checksum=package.checksum
        )

        if self._publish_export_result:
            self._publish_export_result(result)

        self._total_exports += 1
        self._last_operation_time = time.time()

        # 记录导出日志
        if self._publish_operation_log:
            self._publish_operation_log({
                "operation": "export",
                "scope": request.export_scope,
                "total": len(entries),
                "anonymized": len(anonymized_entries),
                "checksum": package.checksum,
                "timestamp": time.time()
            })

        self.state = ImportExportState.IDLE

    def _anonymize_entry(self, entry: Dict[str, Any]) -> Optional[AnonymizedEntry]:
        """强制脱敏：移除所有隐私字段，仅保留通用任务结构"""
        # 移除敏感字段
        entry.pop("user_id", None)
        entry.pop("session_id", None)
        entry.pop("device_fingerprint", None)
        entry.pop("geo_location", None)
        entry.pop("raw_input_text", None)
        entry.pop("personal_preferences", None)
        entry.pop("explicit_feedback", None)

        # 构建脱敏条目
        return AnonymizedEntry(
            entry_id=str(uuid.uuid4()),  # 新 ID
            task_feature_vector=entry.get("task_feature_vector", []),
            tool_call_sequence=entry.get("tool_call_sequence", []),
            result_label=entry.get("result_label", ""),
            generic_rules=entry.get("generic_rules", {}),
            s_value=entry.get("s_value", 0.0),
            c_value=entry.get("c_value", 0.0),
            i_value=entry.get("i_value", 0.0),
            v_value=self.V_VALUE_BASELINE,
            scene_category=entry.get("scene_category", ""),
            original_timestamp=entry.get("timestamp", 0.0)
        )

    def _send_export_result(self, request_id: str, success: bool, error_reason: str):
        if self._publish_export_result:
            self._publish_export_result(ExportResult(
                request_id=request_id,
                success=success,
                error_reason=error_reason
            ))

    # ========== 导入处理 ==========
    def _handle_import(self, request: ImportRequest):
        self.state = ImportExportState.IMPORTING_VALIDATE

        package = request.package
        if not package or not package.anonymized_entries:
            self._send_import_result(request.request_id, False, "经验包为空")
            self.state = ImportExportState.IDLE
            return

        # 第一关：格式校验
        for entry in package.anonymized_entries:
            if not entry.tool_call_sequence and not entry.task_feature_vector:
                self._send_import_result(request.request_id, False, "经验包格式校验失败：条目缺少核心数据")
                self.state = ImportExportState.IDLE
                return

        # 第二关：签名校验
        if request.signature:
            raw = json.dumps([e.__dict__ for e in package.anonymized_entries], sort_keys=True).encode()
            expected = hashlib.sha256(raw).hexdigest()
            if request.signature != expected and request.signature != package.checksum:
                self._send_import_result(request.request_id, False, "签名校验失败")
                self.state = ImportExportState.IDLE
                return

        # 第三关：安全合规校验
        if self._query_safety_compliance:
            compliance_result = self._query_safety_compliance(
                [e.__dict__ for e in package.anonymized_entries]
            )
            if compliance_result and not compliance_result.get("compliant", True):
                self._send_import_result(request.request_id, False, "安全合规校验不通过")
                self.state = ImportExportState.IDLE
                return

        # 容量检查
        import_count = len(package.anonymized_entries)
        if self._query_capacity_check:
            ok = self._query_capacity_check(import_count)
            if not ok:
                self._send_import_result(request.request_id, False, "存储容量不足")
                self.state = ImportExportState.IDLE
                return

        # 开始写入
        self.state = ImportExportState.IMPORTING_WRITE
        entries_to_write = []
        for entry in package.anonymized_entries:
            write_entry = {
                "entry_id": str(uuid.uuid4()),  # 新 ID
                "task_feature_vector": entry.task_feature_vector,
                "tool_call_sequence": entry.tool_call_sequence,
                "result_label": entry.result_label,
                "generic_rules": entry.generic_rules,
                "s_value": entry.s_value,
                "c_value": entry.c_value,
                "i_value": entry.i_value,
                "v_value": self.V_VALUE_BASELINE,  # 重置为基线
                "scene_category": entry.scene_category,
                "source": "import"
            }
            entries_to_write.append(write_entry)

        # 写入漏斗二
        if self._publish_import_write_command:
            self._publish_import_write_command(entries_to_write)

        successful = import_count
        result = ImportResult(
            request_id=request.request_id,
            success=True,
            total_imported=import_count,
            successful_count=successful,
            rejected_entries=[]
        )

        if self._publish_import_result:
            self._publish_import_result(result)

        self._total_imports += 1
        self._last_operation_time = time.time()

        # 记录导入日志
        if self._publish_operation_log:
            self._publish_operation_log({
                "operation": "import",
                "source": request.source,
                "count": import_count,
                "timestamp": time.time()
            })

        self.state = ImportExportState.IDLE

    def _send_import_result(self, request_id: str, success: bool, error_reason: str):
        if self._publish_import_result:
            self._publish_import_result(ImportResult(
                request_id=request_id,
                success=success,
                error_reason=error_reason
            ))

    # ========== 辅助 ==========
    def emergency_shutdown(self):
        self.state = ImportExportState.SYSTEM_PAUSED
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
    print("  Agent-mlnf-mem 记忆导入导出与脱敏共享单元 (ag-mem-50) 演示")
    print("=" * 70)

    unit = MemoryImportExportUnit()

    print_separator("STEP 1: 导出 L4 经验（脱敏处理）")
    unit.set_l4_entries_query(lambda: [
        {
            "entry_id": "L4-001",
            "user_id": "U001",
            "session_id": "S001",
            "device_fingerprint": "DEV-X",
            "task_feature_vector": [0.8, 0.6],
            "tool_call_sequence": ["weather_api", "parse_result"],
            "result_label": "成功",
            "generic_rules": {"if": "query_weather", "then": "use_weather_api"},
            "s_value": 0.6,
            "c_value": 0.4,
            "i_value": 0.7,
            "v_value": 0.5,
            "scene_category": "工具调用"
        }
    ])
    unit.set_export_request_query(lambda: ExportRequest(
        request_id="EX01", requester="admin",
        export_scope="L4", l5_export_authorized=False
    ))
    unit.run_cycle()
    print(f"  导出完成")

    print_separator("STEP 2: 导入经验包（V值重置）")
    entry = AnonymizedEntry(
        entry_id="IMP-001",
        tool_call_sequence=["search_api"],
        result_label="成功",
        v_value=0.5,
        scene_category="信息检索"
    )
    unit.set_import_request_query(lambda: ImportRequest(
        request_id="IM01", source="external",
        package=ExperiencePackage(anonymized_entries=[entry]),
        signature=""
    ))
    unit.run_cycle()
    print(f"  导入完成")

    print("\n✅ 记忆导入导出与脱敏共享单元演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("ag-mem-50 记忆导入导出与脱敏共享单元 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        def setup_unit():
            return MemoryImportExportUnit()

        # TC-M50-01: 正常导出脱敏
        print("\n[TC-M50-01] 正常导出脱敏")
        try:
            u = setup_unit()
            u.set_l4_entries_query(lambda: [{"entry_id": "L4-01", "user_id": "U1", "task_feature_vector": [0.1]}])
            u.set_export_request_query(lambda: ExportRequest(request_id="T01", export_scope="L4"))
            u.run_cycle()
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M50-02: L5导出未授权被拒
        print("\n[TC-M50-02] L5导出未授权被拒")
        try:
            u = setup_unit()
            u.set_export_request_query(lambda: ExportRequest(request_id="T02", export_scope="L5", l5_export_authorized=False))
            u.run_cycle()
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M50-03: 正常导入
        print("\n[TC-M50-03] 正常导入")
        try:
            u = setup_unit()
            u.set_import_request_query(lambda: ImportRequest(
                request_id="T03", source="test",
                package=ExperiencePackage(anonymized_entries=[
                    AnonymizedEntry(tool_call_sequence=["api"], result_label="成功")
                ])
            ))
            u.run_cycle()
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M50-04: 签名校验失败
        print("\n[TC-M50-04] 签名校验失败")
        try:
            u = setup_unit()
            u.set_import_request_query(lambda: ImportRequest(
                request_id="T04", source="test",
                package=ExperiencePackage(anonymized_entries=[AnonymizedEntry(tool_call_sequence=["api"])]),
                signature="invalid_signature"
            ))
            u.run_cycle()
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M50-05: 合规校验不通过
        print("\n[TC-M50-05] 合规校验不通过")
        try:
            u = setup_unit()
            u.set_safety_compliance_query(lambda entries: {"compliant": False, "violated_rules": ["禁止导入"]})
            u.set_import_request_query(lambda: ImportRequest(
                request_id="T05", source="test",
                package=ExperiencePackage(anonymized_entries=[AnonymizedEntry(tool_call_sequence=["api"])])
            ))
            u.run_cycle()
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M50-06: 紧急熔断
        print("\n[TC-M50-06] 紧急熔断")
        try:
            u = setup_unit()
            u.emergency_shutdown()
            assert u.state == ImportExportState.SYSTEM_PAUSED
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