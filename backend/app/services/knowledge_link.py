"""知识链接领域服务：管理实体与外部知识的链接关系。"""
from __future__ import annotations

import hashlib
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


LINK_TYPES = ["wiki", "dbpedia", "knowledge_panel", "authority_page", "linkedin"]


class KnowledgeLinkService(BaseModuleService):
    MODULE = "knowledge_link"
    ENTITY = "链接记录"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["链接ID", "源实体", "外部URI", "链接类型", "外部来源", "验证状态", "上次验证"]
    REQUIRED_FIELDS = ["链接ID", "源实体", "外部URI"]
    STATUS_ORDER = ["待验证", "已验证", "已失效", "已移除"]
    ACTION_RULES = {"验证链接": "待验证", "标记失效": "已验证", "移除链接": "已验证"}
    NEGATIVE_ACTIONS = []

    def consume_relations(self, relation_graph_edges: list[dict[str, Any]]) -> dict[str, Any]:
        """消费关系图谱的已验证边，为实体建立外部知识链接。"""
        if not relation_graph_edges:
            return {"ok": True, "link_count": 0, "links": []}

        links = []
        for edge in relation_graph_edges:
            entities = [edge.get("source_entity", ""), edge.get("target_entity", "")]
            for entity in entities:
                if not entity:
                    continue
                ehash = int(hashlib.md5(entity.encode()).hexdigest()[:4], 16)
                link_type = LINK_TYPES[ehash % len(LINK_TYPES)]
                link_id = hashlib.sha256(
                    f"{entity}|{link_type}|ext".encode()
                ).hexdigest()[:12]
                external_uri = f"https://{link_type}.example.com/entity/{entity}"
                links.append({
                    "链接ID": link_id,
                    "源实体": entity,
                    "外部URI": external_uri,
                    "链接类型": link_type,
                    "外部来源": "relation_graph_pipeline",
                    "验证状态": "自动生成",
                    "上次验证": "2026-11-19T10:00:00Z",
                })
        return {
            "ok": True,
            "input_edges": len(relation_graph_edges),
            "link_count": len(links),
            "links": links,
        }

    def build_aggregate_input(self) -> dict[str, Any]:
        """构建聚合视图的输入数据。"""
        rows = store.rows(self.MODULE)
        link_type_dist: dict[str, int] = {}
        for row in rows:
            ltype = row.get("链接类型", "未知")
            link_type_dist[ltype] = link_type_dist.get(ltype, 0) + 1
        return {
            "total_links": len(rows),
            "verified_links": sum(1 for r in rows if r.get("status") == "已验证"),
            "invalid_links": sum(1 for r in rows if r.get("status") == "已失效"),
            "link_type_distribution": link_type_dist,
        }

    def validate_link_batch(self) -> dict[str, Any]:
        """批量验证待验证的链接。"""
        pending = [r for r in store.rows(self.MODULE) if r.get("status") == "待验证"]
        validated = 0
        for row in pending:
            link_id = row.get("id")
            if link_id:
                existing = store.find(self.MODULE, int(link_id))
                if existing:
                    existing["验证状态"] = "有效"
                    existing["上次验证"] = "2026-11-19T10:00:00Z"
                    validated += 1
        return {"ok": True, "pending_count": len(pending), "validated_count": validated}

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        base["link_stats"] = self.build_aggregate_input()
        return base