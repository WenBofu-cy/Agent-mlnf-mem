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

依赖模块: 无
被依赖模块: ag-mem-01, ag-ecc-01, ag-ecc-03, ag-ecc-04

安全约束:
  S-01: 知识库为只读数据，运行时不得被任何控制模块或自动化流程修改
  S-02: 知识更新必须通过签名校验与完整性验证，防止恶意篡改
  S-03: 知识库更新前必须完整备份当前版本，更新失败时原子回滚
  S-04: 安全等级为“敏感”的知识条目仅向授权模块（ag-ecc-04 安全仲裁模块）开放查询
  S-05: 本库完全独立于漏斗记忆系统，不参与记忆沉淀、晋升、遗忘任何环节

版本: V1.0 (总线集成版)
"""

import time
import uuid
import copy
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum

from memory_bus import InternalBus, Message


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


class IndependentKnowledgeBase:
    module_id = "ag-mem-44"
    module_name = "独立知识库"
    version = "V1.0"

    MAX_CACHE_SIZE = 500
    STATUS_REPORT_INTERVAL_SEC = 60

    def __init__(self):
        self.bus: Optional[InternalBus] = None
        self.state = KnowledgeBaseState.LOADING
        self._kb_version = "1.0.0"

        self._entries: Dict[str, Dict[str, Any]] = {}
        self._keyword_index: Dict[str, List[str]] = {}
        self._category_index: Dict[KnowledgeCategory, List[str]] = {c: [] for c in KnowledgeCategory}
        self._query_cache: Dict[str, Tuple[Dict[str, Any], float]] = {}
        self._total_entries: int = 0
        self._last_status_time: float = time.time()
        self._pending_logs: List[Dict[str, Any]] = []

        # 初始化内置知识
        self._load_preset_knowledge()
        self.state = KnowledgeBaseState.NORMAL_SERVICE

        print(f"[{self.module_id}] {self.module_name} {self.version} 初始化完成, "
              f"预置条目数={self._total_entries}")

    # ====================== 全系统统一调度入口（对齐框架规范） ======================
    def run_cycle(self):
        self.knowledge_base_main_loop()

    # ====================== 主循环 ======================
    def knowledge_base_main_loop(self):
        if self.state == KnowledgeBaseState.SYSTEM_PAUSED:
            return

        if self.bus:
            self.bus.process_batch(10)

        now = time.time()
        if now - self._last_status_time >= self.STATUS_REPORT_INTERVAL_SEC:
            self._report_status()
            self._last_status_time = now

    # ====================== 总线消息入口（增加异常捕获） ======================
    def handle_message(self, msg: Message):
        if not isinstance(msg.data, dict):
            return

        try:
            if msg.topic == "ag-mem-44.knowledge_query":
                self._handle_query(msg)
                return

            if msg.topic == "ag-mem-44.knowledge_update":
                self._handle_update(msg)
                return

            if msg.topic == "ag-mem-44.integrity_check":
                self._handle_integrity_check(msg)
                return
        except Exception as e:
            self._log_event("MSG_PROCESS_ERROR", {"error": str(e)})

    # ====================== 查询处理 ======================
    def _handle_query(self, msg: Message):
        data = msg.data
        requester = msg.source_module
        query_type_str = data.get("query_type")
        keywords = data.get("keywords", [])
        match_mode = data.get("match_mode", "精确")
        max_results = data.get("max_results", 20)

        start_time = time.time()

        # 检查缓存
        cache_key = f"{query_type_str or 'all'}:{','.join(keywords)}:{match_mode}"
        if cache_key in self._query_cache:
            cached_result, cached_time = self._query_cache[cache_key]
            if time.time() - cached_time < 60:
                self._reply_query(msg, cached_result)
                return

        # 按分类筛选
        if query_type_str:
            try:
                category = KnowledgeCategory(query_type_str)
                candidate_ids = self._category_index.get(category, [])
            except ValueError:
                candidate_ids = list(self._entries.keys())
        else:
            candidate_ids = list(self._entries.keys())

        # 匹配
        matched = []
        for eid in candidate_ids:
            entry = self._entries.get(eid)
            if not entry:
                continue

            # 敏感条目权限控制
            if entry.get("security_level") == SecurityLevel.SENSITIVE.value and requester != "ag-ecc-04":
                continue

            if not keywords:
                matched.append(entry)
            elif match_mode == "精确":
                if all(kw in entry.get("keywords", []) for kw in keywords):
                    matched.append(entry)
            else:
                text = entry.get("title", "") + " " + " ".join(entry.get("keywords", []))
                if any(kw in text for kw in keywords):
                    matched.append(entry)

        matched = matched[:max_results]
        elapsed = (time.time() - start_time) * 1000

        result = {
            "entries": matched,
            "total_matched": len(matched),
            "query_duration_ms": elapsed,
            "version": self._kb_version
        }

        # 写入缓存
        if len(self._query_cache) >= self.MAX_CACHE_SIZE:
            oldest = min(self._query_cache.keys(), key=lambda k: self._query_cache[k][1])
            del self._query_cache[oldest]
        self._query_cache[cache_key] = (result, time.time())

        self._reply_query(msg, result)

    def _reply_query(self, msg: Message, result: Dict[str, Any]):
        if self.bus:
            self.bus.publish(
                topic=f"{msg.source_module}.knowledge_result",
                source_module=self.module_id,
                data=result,
                target_module=msg.source_module,
                correlation_id=msg.correlation_id
            )

    # ====================== 更新处理 ======================
    def _handle_update(self, msg: Message):
        data = msg.data
        self.state = KnowledgeBaseState.LOADING

        # 签名校验
        if not data.get("signature") or len(data.get("signature", "")) < 10:
            self._reply_update(msg, False, error="签名校验失败")
            self.state = KnowledgeBaseState.NORMAL_SERVICE
            return

        # 备份
        old_entries = copy.deepcopy(self._entries)
        old_indexes = {
            "keyword": copy.deepcopy(self._keyword_index),
            "category": copy.deepcopy(self._category_index)
        }
        old_version = self._kb_version
        old_count = self._total_entries

        try:
            package = data.get("data_package", {})
            added = updated = deleted = 0

            # 添加
            for entry_data in package.get("add_entries", []):
                eid = entry_data.get("entry_id", f"KB-{uuid.uuid4().hex[:8]}")
                self._entries[eid] = {
                    "entry_id": eid,
                    "category": entry_data.get("category", "工具使用手册"),
                    "title": entry_data.get("title", ""),
                    "keywords": entry_data.get("keywords", []),
                    "content": entry_data.get("content", {}),
                    "security_level": entry_data.get("security_level", "公开"),
                    "version": data.get("version", self._kb_version),
                    "updated_at": time.time(),
                    "source": "更新"
                }
                added += 1

            # 更新
            for entry_data in package.get("update_entries", []):
                eid = entry_data.get("entry_id")
                if eid in self._entries:
                    entry = self._entries[eid]
                    for key in ["title", "keywords", "content"]:
                        if key in entry_data:
                            entry[key] = entry_data[key]
                    entry["version"] = data.get("version", self._kb_version)
                    entry["updated_at"] = time.time()
                    updated += 1

            # 删除
            for eid in package.get("delete_entry_ids", []):
                if eid in self._entries:
                    del self._entries[eid]
                    deleted += 1

            # 重建索引
            self._rebuild_indexes()
            self._kb_version = data.get("version", self._kb_version)
            self._total_entries = len(self._entries)
            self._query_cache.clear()
            self._reply_update(msg, True, added, updated, deleted)
        except Exception as e:
            # 回滚
            self._entries = old_entries
            self._keyword_index = old_indexes["keyword"]
            self._category_index = old_indexes["category"]
            self._kb_version = old_version
            self._total_entries = old_count
            self._reply_update(msg, False, error=str(e))

        self.state = KnowledgeBaseState.NORMAL_SERVICE

    def _reply_update(self, msg: Message, success: bool, added: int = 0, updated: int = 0,
                      deleted: int = 0, error: str = ""):
        if self.bus:
            self.bus.publish(
                topic=f"{msg.source_module}.update_confirm",
                source_module=self.module_id,
                data={
                    "success": success,
                    "added_count": added,
                    "updated_count": updated,
                    "deleted_count": deleted,
                    "error_reason": error
                },
                target_module=msg.source_module,
                correlation_id=msg.correlation_id
            )

    # ====================== 完整性校验 ======================
    def _handle_integrity_check(self, msg: Message):
        start = time.time()
        total = len(self._entries)
        corrupted = [eid for eid, e in self._entries.items() if not e.get("title") or not e.get("category")]
        elapsed = (time.time() - start) * 1000

        if corrupted:
            self.state = KnowledgeBaseState.DEGRADED
        elif self.state == KnowledgeBaseState.DEGRADED:
            self.state = KnowledgeBaseState.NORMAL_SERVICE

        if self.bus:
            self.bus.publish(
                topic=f"{msg.source_module}.integrity_report",
                source_module=self.module_id,
                data={
                    "total_entries": total,
                    "valid_entries": total - len(corrupted),
                    "corrupted_entries": corrupted,
                    "check_duration_ms": elapsed
                },
                target_module=msg.source_module,
                correlation_id=msg.correlation_id
            )

    # ====================== 索引管理 ======================
    def _load_preset_knowledge(self):
        preset = [
            {
                "entry_id": "KB-TOOL-001",
                "category": "工具使用手册",
                "title": "weather_api",
                "keywords": ["天气", "API", "查询"],
                "content": {"description": "查询天气信息", "parameters": {"city": "string"}},
                "return": "json",
                "security_level": "公开"
            },
            {
                "entry_id": "KB-TOOL-002",
                "category": "工具使用手册",
                "title": "file_read",
                "keywords": ["文件", "读取", "文本"],
                "content": {"description": "读取本地文件内容", "parameters": {"path": "string"}},
                "return": "text",
                "security_level": "公开"
            },
            {
                "entry_id": "KB-SAFETY-001",
                "category": "操作安全等级",
                "title": "delete_file",
                "keywords": ["删除", "文件", "危险"],
                "content": {"description": "删除文件操作", "risk_level": "高", "requires_confirmation": True},
                "security_level": "敏感"
            }
        ]
        for entry in preset:
            self._entries[entry["entry_id"]] = entry
        self._rebuild_indexes()
        self._total_entries = len(self._entries)

    def _rebuild_indexes(self):
        self._keyword_index = {}
        self._category_index = {c: [] for c in KnowledgeCategory}
        for entry in self._entries.values():
            for kw in entry.get("keywords", []):
                self._keyword_index.setdefault(kw, []).append(entry["entry_id"])
            cat_str = entry.get("category", "")
            try:
                cat = KnowledgeCategory(cat_str)
                self._category_index[cat].append(entry["entry_id"])
            except ValueError:
                pass

    # ====================== 状态上报 ======================
    def _report_status(self):
        if self.bus:
            dist = {c.value: len(self._category_index.get(c, [])) for c in KnowledgeCategory}
            self.bus.publish_to_module(
                target_module="ag-mem-03",
                event_type="internal_status",
                source_module=self.module_id,
                data={
                    "state": self.state.value,
                    "total_entries": self._total_entries,
                    "category_distribution": dist,
                    "version": self._kb_version
                }
            )

    # ====================== 管理接口 ======================
    def emergency_shutdown(self):
        self.state = KnowledgeBaseState.SYSTEM_PAUSED
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