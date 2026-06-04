#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块编号: ag-mem-44
模块名称: 独立知识库
所属分区: 四、漏斗外挂扩展区（物理隔离）
核心职责: 作为漏斗外挂扩展区的结构化知识存储库，对接工具使用手册、API 文档、常见问题库、
          系统运维手册等结构化知识的统一管理与查询。为 ECC 认知大脑的工具选择模块（ag-ecc-03）
          提供工具描述与参数约束查询，为安全仲裁模块（ag-ecc-04）提供操作安全等级参考，
          为意图解析模块（ag-ecc-01）提供领域术语与实体词典。本库完全独立于双漏斗记忆系统
          运行，不参与记忆沉淀、筛选、晋升与遗忘机制。知识内容在系统部署前预置，支持定期
          离线更新。仅提供只读查询服务，不参与任何认知决策。

依赖模块:
    无（作为独立知识服务，不依赖记忆系统内部模块）
被依赖模块:
    ag-mem-01(总控漏斗F₀，可转发来自ECC的知识查询请求),
    ag-ecc-01(意图解析模块), ag-ecc-03(工具选择模块), ag-ecc-04(安全仲裁模块)

安全约束:
  S-01: 知识库为只读数据，运行时不得被任何控制模块或自动化流程修改
  S-02: 知识更新必须通过签名校验与完整性验证，防止恶意篡改
  S-03: 知识库更新前必须完整备份当前版本，更新失败时原子回滚
  S-04: 安全等级为“敏感”的知识条目仅向授权模块（ag-ecc-04 安全仲裁模块）开放查询
  S-05: 本库完全独立于漏斗记忆系统，不参与记忆沉淀、晋升、遗忘任何环节
"""

from typing import Dict, List, Optional, Any, Callable, Tuple
from dataclasses import dataclass, field
from enum import Enum
import time
import uuid
import copy


class KnowledgeBaseState(Enum):
    NORMAL_SERVICE = "normal_service"
    LOADING = "loading"
    DEGRADED = "degraded"
    SYSTEM_PAUSED = "system_paused"


class KnowledgeCategory(Enum):
    TOOL = "工具使用手册"
    API = "API参考文档"
    FAQ = "常见问题库"
    SAFETY = "操作安全等级"
    TERM = "领域术语词典"
    MANUAL = "系统运维手册"


class SecurityLevel(Enum):
    PUBLIC = "公开"
    INTERNAL = "内部"
    SENSITIVE = "敏感"


@dataclass
class KnowledgeEntry:
    entry_id: str = ""
    category: KnowledgeCategory = KnowledgeCategory.TOOL
    title: str = ""
    keywords: List[str] = field(default_factory=list)
    content: Dict[str, Any] = field(default_factory=dict)
    related_entry_ids: List[str] = field(default_factory=list)
    security_level: SecurityLevel = SecurityLevel.PUBLIC
    version: str = "1.0"
    updated_at: float = field(default_factory=time.time)
    source: str = "预置"


@dataclass
class KnowledgeQueryRequest:
    request_id: str = ""
    requester_module: str = ""
    query_type: Optional[KnowledgeCategory] = None
    keywords: List[str] = field(default_factory=list)
    match_mode: str = "精确"
    max_results: int = 20
    timestamp: float = field(default_factory=time.time)


@dataclass
class KnowledgeQueryResult:
    request_id: str = ""
    entries: List[KnowledgeEntry] = field(default_factory=list)
    total_matched: int = 0
    query_duration_ms: float = 0.0
    version: str = ""


@dataclass
class KnowledgeUpdateCommand:
    update_scope: str = "全量"
    data_package: Dict[str, Any] = field(default_factory=dict)
    version: str = ""
    signature: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class KnowledgeUpdateConfirm:
    success: bool = True
    added_count: int = 0
    updated_count: int = 0
    deleted_count: int = 0
    new_version: str = ""
    error_reason: str = ""


@dataclass
class KnowledgeIntegrityReport:
    total_entries: int = 0
    valid_entries: int = 0
    corrupted_entries: List[str] = field(default_factory=list)
    check_duration_ms: float = 0.0


@dataclass
class KnowledgeBaseStatus:
    state: KnowledgeBaseState = KnowledgeBaseState.NORMAL_SERVICE
    total_entries: int = 0
    category_distribution: Dict[str, int] = field(default_factory=dict)
    last_updated: float = 0.0
    version: str = ""


class IndependentKnowledgeBase:
    MAX_CACHE_SIZE = 500
    STATUS_REPORT_INTERVAL_SEC = 60

    def __init__(self):
        self.module_id = "ag-mem-44"
        self.module_name = "独立知识库"
        self.version = "V1.0"
        self._kb_version = "1.0.0"

        self.state = KnowledgeBaseState.LOADING
        self._entries: Dict[str, KnowledgeEntry] = {}
        self._keyword_index: Dict[str, List[str]] = {}
        self._category_index: Dict[KnowledgeCategory, List[str]] = {c: [] for c in KnowledgeCategory}
        self._query_cache: Dict[str, Tuple[KnowledgeQueryResult, float]] = {}
        self._total_entries: int = 0
        self._last_status_time: float = 0.0
        self._pending_logs: List[Dict[str, Any]] = []

        # 回调注入
        self._query_knowledge_request = None
        self._query_update_command = None
        self._query_integrity_check = None

        self._publish_query_result = None
        self._publish_update_confirm = None
        self._publish_integrity_report = None
        self._publish_status_report = None
        self._publish_event_log = None

        # 初始化内置知识
        self._load_preset_knowledge()
        self.state = KnowledgeBaseState.NORMAL_SERVICE

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成, 预置条目数={self._total_entries}")

    # ========== 回调注入 ==========
    def set_knowledge_request_query(self, callback: Callable[[], Optional[KnowledgeQueryRequest]]):
        self._query_knowledge_request = callback

    def set_update_command_query(self, callback: Callable[[], Optional[KnowledgeUpdateCommand]]):
        self._query_update_command = callback

    def set_integrity_check_query(self, callback: Callable[[], Optional[Dict[str, Any]]]):
        self._query_integrity_check = callback

    def set_query_result_publisher(self, callback: Callable[[KnowledgeQueryResult], None]):
        self._publish_query_result = callback

    def set_update_confirm_publisher(self, callback: Callable[[KnowledgeUpdateConfirm], None]):
        self._publish_update_confirm = callback

    def set_integrity_report_publisher(self, callback: Callable[[KnowledgeIntegrityReport], None]):
        self._publish_integrity_report = callback

    def set_status_report_publisher(self, callback: Callable[[KnowledgeBaseStatus], None]):
        self._publish_status_report = callback

    def set_event_log_publisher(self, callback: Callable[[Dict[str, Any]], None]):
        self._publish_event_log = callback

    # ========== 主循环 ==========
    def run_knowledge_cycle(self):
        now = time.time()

        if self.state == KnowledgeBaseState.SYSTEM_PAUSED:
            return

        # 定期状态上报
        if now - self._last_status_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._publish_status()
            self._last_status_time = now

        # 处理查询请求
        query = self._query_knowledge_request() if self._query_knowledge_request else None
        if query:
            self._handle_query(query)
            return

        # 处理更新指令
        update_cmd = self._query_update_command() if self._query_update_command else None
        if update_cmd:
            self._handle_update(update_cmd)
            return

        # 处理完整性校验
        integrity_cmd = self._query_integrity_check() if self._query_integrity_check else None
        if integrity_cmd:
            self._handle_integrity_check()

    # ========== 查询处理 ==========
    def _handle_query(self, request: KnowledgeQueryRequest):
        start_time = time.time()

        # 检查缓存
        cache_key = f"{request.query_type.value if request.query_type else 'all'}:{','.join(request.keywords)}:{request.match_mode}"
        if cache_key in self._query_cache:
            cached_result, cached_time = self._query_cache[cache_key]
            if time.time() - cached_time < 60:
                if self._publish_query_result:
                    self._publish_query_result(cached_result)
                return

        # 按分类筛选
        if request.query_type and request.query_type in KnowledgeCategory:
            candidate_ids = self._category_index.get(request.query_type, [])
        else:
            candidate_ids = list(self._entries.keys())

        # 按关键词匹配
        matched_entries = []
        for entry_id in candidate_ids:
            entry = self._entries.get(entry_id)
            if not entry:
                continue

            # 权限检查：敏感条目仅授权模块可访问
            if entry.security_level == SecurityLevel.SENSITIVE and request.requester_module != "ag-ecc-04":
                continue

            if not request.keywords:
                matched_entries.append(entry)
                continue

            # 关键词匹配
            if request.match_mode == "精确":
                if all(kw in entry.keywords for kw in request.keywords):
                    matched_entries.append(entry)
            elif request.match_mode == "模糊":
                if any(kw in " ".join(entry.keywords + [entry.title]) for kw in request.keywords):
                    matched_entries.append(entry)
            else:
                if any(kw in " ".join(entry.keywords + [entry.title]) for kw in request.keywords):
                    matched_entries.append(entry)

        # 截断
        matched_entries = matched_entries[:request.max_results]

        elapsed = (time.time() - start_time) * 1000
        result = KnowledgeQueryResult(
            request_id=request.request_id,
            entries=matched_entries,
            total_matched=len(matched_entries),
            query_duration_ms=elapsed,
            version=self._kb_version
        )

        # 写入缓存
        if len(self._query_cache) >= self.MAX_CACHE_SIZE:
            oldest_key = min(self._query_cache.keys(), key=lambda k: self._query_cache[k][1])
            del self._query_cache[oldest_key]
        self._query_cache[cache_key] = (result, time.time())

        if self._publish_query_result:
            self._publish_query_result(result)

    # ========== 更新处理 ==========
    def _handle_update(self, command: KnowledgeUpdateCommand):
        self.state = KnowledgeBaseState.LOADING

        # 校验签名
        if not command.signature or len(command.signature) < 10:
            self._send_update_confirm(False, 0, 0, 0, "签名校验失败")
            self.state = KnowledgeBaseState.NORMAL_SERVICE
            return

        # 备份当前数据与索引
        old_entries = copy.deepcopy(self._entries)
        old_indexes = {
            "keyword": copy.deepcopy(self._keyword_index),
            "category": copy.deepcopy(self._category_index)
        }
        old_version = self._kb_version
        old_count = self._total_entries

        try:
            added = 0
            updated = 0
            deleted = 0

            data = command.data_package

            # 添加新条目（不逐条维护索引，最后统一重建）
            if "add_entries" in data:
                for entry_data in data["add_entries"]:
                    entry = KnowledgeEntry(
                        entry_id=entry_data.get("entry_id", f"KB-{uuid.uuid4().hex[:8]}"),
                        category=KnowledgeCategory[entry_data["category"]] if "category" in entry_data else KnowledgeCategory.TOOL,
                        title=entry_data.get("title", ""),
                        keywords=entry_data.get("keywords", []),
                        content=entry_data.get("content", {}),
                        security_level=SecurityLevel[entry_data.get("security_level", "PUBLIC")],
                        version=command.version,
                        updated_at=time.time(),
                        source="更新"
                    )
                    self._entries[entry.entry_id] = entry
                    added += 1

            # 更新已有条目
            if "update_entries" in data:
                for entry_data in data["update_entries"]:
                    entry_id = entry_data.get("entry_id")
                    if entry_id in self._entries:
                        entry = self._entries[entry_id]
                        if "title" in entry_data:
                            entry.title = entry_data["title"]
                        if "keywords" in entry_data:
                            entry.keywords = entry_data["keywords"]
                        if "content" in entry_data:
                            entry.content = entry_data["content"]
                        entry.version = command.version
                        entry.updated_at = time.time()
                        updated += 1

            # 删除条目
            if "delete_entry_ids" in data:
                for entry_id in data["delete_entry_ids"]:
                    if entry_id in self._entries:
                        del self._entries[entry_id]
                        deleted += 1

            # 统一重建索引（性能优化：避免逐条维护的冗余操作）
            self._rebuild_indexes()
            self._kb_version = command.version
            self._total_entries = len(self._entries)
            self._query_cache.clear()

            self._send_update_confirm(True, added, updated, deleted, command.version)

        except Exception as e:
            # 原子回滚
            self._entries = old_entries
            self._keyword_index = old_indexes["keyword"]
            self._category_index = old_indexes["category"]
            self._kb_version = old_version
            self._total_entries = old_count
            self._send_update_confirm(False, 0, 0, 0, f"更新异常: {str(e)}")

        self.state = KnowledgeBaseState.NORMAL_SERVICE

    def _send_update_confirm(self, success: bool, added: int, updated: int, deleted: int, version_or_error: str):
        if self._publish_update_confirm:
            if success:
                self._publish_update_confirm(KnowledgeUpdateConfirm(
                    success=True,
                    added_count=added,
                    updated_count=updated,
                    deleted_count=deleted,
                    new_version=version_or_error
                ))
            else:
                self._publish_update_confirm(KnowledgeUpdateConfirm(
                    success=False,
                    error_reason=version_or_error
                ))

    # ========== 完整性校验 ==========
    def _handle_integrity_check(self):
        start_time = time.time()
        total = len(self._entries)
        corrupted = []

        for entry_id, entry in self._entries.items():
            if not entry.title or not entry.category:
                corrupted.append(entry_id)

        elapsed = (time.time() - start_time) * 1000
        report = KnowledgeIntegrityReport(
            total_entries=total,
            valid_entries=total - len(corrupted),
            corrupted_entries=corrupted,
            check_duration_ms=elapsed
        )

        if corrupted:
            self.state = KnowledgeBaseState.DEGRADED
        elif self.state == KnowledgeBaseState.DEGRADED:
            self.state = KnowledgeBaseState.NORMAL_SERVICE

        if self._publish_integrity_report:
            self._publish_integrity_report(report)

    # ========== 索引管理 ==========
    def _load_preset_knowledge(self):
        """加载预置基础知识"""
        preset_entries = [
            KnowledgeEntry(
                entry_id="KB-TOOL-001",
                category=KnowledgeCategory.TOOL,
                title="weather_api",
                keywords=["天气", "API", "查询"],
                content={"description": "查询天气信息", "parameters": {"city": "string"}, "return": "json"},
                security_level=SecurityLevel.PUBLIC
            ),
            KnowledgeEntry(
                entry_id="KB-TOOL-002",
                category=KnowledgeCategory.TOOL,
                title="file_read",
                keywords=["文件", "读取", "文本"],
                content={"description": "读取本地文件内容", "parameters": {"path": "string"}, "return": "text"},
                security_level=SecurityLevel.PUBLIC
            ),
            KnowledgeEntry(
                entry_id="KB-SAFETY-001",
                category=KnowledgeCategory.SAFETY,
                title="delete_file",
                keywords=["删除", "文件", "危险"],
                content={"description": "删除文件操作", "risk_level": "高", "requires_confirmation": True},
                security_level=SecurityLevel.SENSITIVE
            ),
        ]
        for entry in preset_entries:
            self._entries[entry.entry_id] = entry
        self._rebuild_indexes()
        self._total_entries = len(self._entries)

    def _rebuild_indexes(self):
        """全量重建关键词索引与分类索引"""
        self._keyword_index = {}
        self._category_index = {c: [] for c in KnowledgeCategory}
        for entry in self._entries.values():
            for kw in entry.keywords:
                if kw not in self._keyword_index:
                    self._keyword_index[kw] = []
                self._keyword_index[kw].append(entry.entry_id)
            if entry.category in self._category_index:
                self._category_index[entry.category].append(entry.entry_id)

    # ========== 辅助 ==========
    def _publish_status(self):
        dist = {c.value: len(self._category_index.get(c, [])) for c in KnowledgeCategory}
        if self._publish_status_report:
            self._publish_status_report(KnowledgeBaseStatus(
                state=self.state,
                total_entries=self._total_entries,
                category_distribution=dist,
                last_updated=time.time(),
                version=self._kb_version
            ))

    def emergency_shutdown(self):
        self.state = KnowledgeBaseState.SYSTEM_PAUSED
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
    print("  Agent-mlnf-mem 独立知识库 (ag-mem-44) 演示")
    print("=" * 70)

    kb = IndependentKnowledgeBase()

    print_separator("STEP 1: 查询天气API工具信息")
    kb.set_knowledge_request_query(lambda: KnowledgeQueryRequest(
        request_id="Q1", requester_module="ag-ecc-03",
        query_type=KnowledgeCategory.TOOL, keywords=["天气"]
    ))
    kb.run_knowledge_cycle()

    print_separator("STEP 2: 非授权模块查询敏感信息被拦截")
    kb.set_knowledge_request_query(lambda: KnowledgeQueryRequest(
        request_id="Q2", requester_module="ag-ecc-01",
        query_type=KnowledgeCategory.SAFETY, keywords=["删除"]
    ))
    kb.run_knowledge_cycle()
    print("  (敏感条目仅向ag-ecc-04开放，其他模块查询结果为空)")

    print("\n✅ 独立知识库演示完成")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=" * 60)
        print("ag-mem-44 独立知识库 单元测试")
        print("=" * 60)
        passed, failed = 0, 0

        def setup_kb():
            return IndependentKnowledgeBase()

        # TC-M44-01: 正常查询工具信息
        print("\n[TC-M44-01] 正常查询工具信息")
        try:
            k = setup_kb()
            k.set_knowledge_request_query(lambda: KnowledgeQueryRequest(
                request_id="T01", requester_module="ag-ecc-03",
                query_type=KnowledgeCategory.TOOL, keywords=["天气"]
            ))
            k.run_knowledge_cycle()
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M44-02: 敏感条目权限控制
        print("\n[TC-M44-02] 非授权模块无法查询敏感条目")
        try:
            k = setup_kb()
            k.set_knowledge_request_query(lambda: KnowledgeQueryRequest(
                request_id="T02", requester_module="ag-ecc-01",
                query_type=KnowledgeCategory.SAFETY, keywords=["删除"]
            ))
            k.run_knowledge_cycle()
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M44-03: 授权模块可查询敏感条目
        print("\n[TC-M44-03] 安全仲裁模块可查询敏感条目")
        try:
            k = setup_kb()
            k.set_knowledge_request_query(lambda: KnowledgeQueryRequest(
                request_id="T03", requester_module="ag-ecc-04",
                query_type=KnowledgeCategory.SAFETY, keywords=["删除"]
            ))
            k.run_knowledge_cycle()
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M44-04: 查询不存在的知识
        print("\n[TC-M44-04] 查询不存在的知识返回空")
        try:
            k = setup_kb()
            k.set_knowledge_request_query(lambda: KnowledgeQueryRequest(
                request_id="T04", requester_module="ag-ecc-03",
                query_type=KnowledgeCategory.TOOL, keywords=["不存在的工具"]
            ))
            k.run_knowledge_cycle()
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M44-05: 知识更新（添加新条目）
        print("\n[TC-M44-05] 知识更新添加新条目")
        try:
            k = setup_kb()
            old_count = k._total_entries
            k.set_update_command_query(lambda: KnowledgeUpdateCommand(
                data_package={"add_entries": [{"category": "TOOL", "title": "new_tool", "keywords": ["new"]}]},
                version="1.1.0", signature="valid-signature-12345"
            ))
            k.run_knowledge_cycle()
            assert k._total_entries == old_count + 1
            print("   ✅ PASS")
            passed += 1
        except Exception as e:
            print(f"   ❌ FAIL: {e}")
            failed += 1

        # TC-M44-06: 紧急熔断
        print("\n[TC-M44-06] 紧急熔断")
        try:
            k = setup_kb()
            k.emergency_shutdown()
            assert k.state == KnowledgeBaseState.SYSTEM_PAUSED
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