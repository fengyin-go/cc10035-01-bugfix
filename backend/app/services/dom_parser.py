"""DOM解析领域服务：管理DOM解析配置，驱动下游实体抽取流水线。"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


CSS_SELECTOR_RE = re.compile(r"^[.#]?[a-zA-Z_][\w\-]*(?:\s*[>+~]\s*[.#]?[a-zA-Z_][\w\-]*)*$")
XPath_RE = re.compile(r"^(//|[.]/|/)[\w\[\]@='\"\-,.()*/]+")


class DomParserService(BaseModuleService):
    MODULE = "dom_parser"
    ENTITY = "解析配置"
    OPERATION_TERM = "解析"
    LIST_FIELDS = ["配置名称", "目标URI", "解析引擎", "选择器规则", "输出Schema", "等待策略", "渲染模式"]
    REQUIRED_FIELDS = ["配置名称", "目标URI", "解析引擎"]
    STATUS_ORDER = ["草稿", "已配置", "解析中", "已停止"]
    ACTION_RULES = {"确认配置": "草稿", "启动解析": "已配置", "停止解析": "解析中"}
    NEGATIVE_ACTIONS = []

    def validate_selector(self, selector_rule: str) -> dict[str, Any]:
        """校验选择器规则语法，返回类型与合法标志。"""
        is_css = bool(CSS_SELECTOR_RE.match(selector_rule.strip()))
        is_xpath = bool(XPath_RE.match(selector_rule.strip()))
        return {
            "selector": selector_rule,
            "syntax_type": "css" if is_css else ("xpath" if is_xpath else "unknown"),
            "valid": is_css or is_xpath,
        }

    def simulate_parse(self, entry: dict[str, Any]) -> dict[str, Any]:
        """模拟一次DOM解析，输出解析字段摘要和哈希签名。"""
        uri = entry.get("目标URI", "")
        engine = entry.get("解析引擎", "beautifulsoup")
        selector = entry.get("选择器规则", "")
        schema = entry.get("输出Schema", "")
        selectors = [s.strip() for s in selector.split(",") if s.strip()]
        fields = [f.strip() for f in schema.split(",") if f.strip()]
        parser_hash = hashlib.sha256(
            f"{uri}|{engine}|{selector}|{schema}".encode()
        ).hexdigest()[:12]
        return {
            "config_name": entry.get("配置名称"),
            "target_uri": uri,
            "parser_engine": engine,
            "selector_count": len(selectors),
            "schema_fields": fields,
            "dom_hash": parser_hash,
            "extracted_records": 0,
        }

    def feed_entity_extract(self, entry_id: int) -> dict[str, Any]:
        """将解析结果输出为实体抽取的输入：被 entity_extract 消费。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "reason": "解析配置不存在"}
        parse_result = self.simulate_parse(entry)
        source_uri = parse_result["target_uri"]
        # 从store读取entity_extract模块数据，计算当前URI已有实体
        existing = [
            r for r in store.rows("entity_extract")
            if r.get("页面来源", "") == source_uri
        ]
        return {
            "ok": True,
            "config_id": entry_id,
            "source_uri": source_uri,
            "parse_hash": parse_result["dom_hash"],
            "fields_for_extraction": parse_result["schema_fields"],
            "existing_entity_count": len(existing),
        }

    def compute_parse_quality(self, entry_id: int) -> dict[str, Any]:
        """计算解析质量指标，被质量检查模块消费。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "reason": "配置不存在"}
        selector = entry.get("选择器规则", "")
        selectors = [s.strip() for s in selector.split(",") if s.strip()]
        uri = entry.get("目标URI", "")
        return {
            "config_id": entry_id,
            "selector_validity_ratio": 1.0 if selectors else 0.0,
            "selector_count": len(selectors),
            "uri_format_valid": uri.startswith("http") if uri else False,
            "has_schema": bool(entry.get("输出Schema", "").strip()),
        }

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        rows = store.rows(self.MODULE)
        base["parser_quality"] = {
            "total_configs": len(rows),
            "with_selectors": sum(1 for r in rows if r.get("选择器规则", "").strip()),
            "with_schema": sum(1 for r in rows if r.get("输出Schema", "").strip()),
            "valid_uris": sum(1 for r in rows if (r.get("目标URI", "") or "").startswith("http")),
        }
        return base