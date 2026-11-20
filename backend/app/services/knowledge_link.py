"""知识链接领域服务：管理实体与外部知识的链接关系。

跨模块流水线（关系图谱 -> 知识链接）的关键约束在这里落地：

- 同一实体与外部 URI 只能有一条已发布（有效）链接，外链身份由实体确定性派生，
  批次重放只认第一次生成的外链身份，并发提交也不会多写；
- 每条链接维护出处台账（批次 ID、关系边 ID、来源页），去重后仍可按原关系回查；
- 链接生成后处于「待回执」，只有覆盖整批的出处回执回到关系图谱并核销批次后，
  条目才标记为已交付，来源无法回查的条目不允许当作已交付。
"""
from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


LINK_TYPES = ["wiki", "dbpedia", "knowledge_panel", "authority_page", "linkedin"]

# 已发布（有效）状态：处于这些状态的 (源实体, 外部URI) 组合全局唯一
ACTIVE_STATUSES = ("待验证", "已验证")
RECEIPT_PENDING = "待回执"
RECEIPT_DELIVERED = "已交付"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class KnowledgeLinkService(BaseModuleService):
    MODULE = "knowledge_link"
    ENTITY = "链接记录"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["链接ID", "源实体", "外部URI", "链接类型", "外部来源", "验证状态", "上次验证"]
    REQUIRED_FIELDS = ["链接ID", "源实体", "外部URI"]
    STATUS_ORDER = ["待验证", "已验证", "已失效", "已移除"]
    ACTION_RULES = {"验证链接": "待验证", "标记失效": "已验证", "移除链接": "已验证"}
    NEGATIVE_ACTIONS = []

    def __init__(self) -> None:
        # 流水线可能并发提交，所有台账与链接行写入都在同一把锁内完成
        self._lock = threading.RLock()
        # 已交付链接的出处台账：链接ID -> 链接身份 + 各条原始关系边出处
        self._provenance: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # 外链身份：实体 -> (链接ID, 链接类型, 外部URI)，全程确定性派生
    # ------------------------------------------------------------------
    @staticmethod
    def _link_identity(entity: str) -> dict[str, str]:
        ehash = int(hashlib.md5(entity.encode("utf-8")).hexdigest()[:4], 16)
        link_type = LINK_TYPES[ehash % len(LINK_TYPES)]
        external_uri = f"https://{link_type}.example.com/entity/{entity}"
        link_id = hashlib.sha256(
            f"{entity}|{link_type}|ext".encode("utf-8")
        ).hexdigest()[:12]
        return {"链接ID": link_id, "链接类型": link_type, "外部URI": external_uri}

    @staticmethod
    def _link_summary(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "链接ID": row.get("链接ID"),
            "源实体": row.get("源实体"),
            "外部URI": row.get("外部URI"),
            "链接类型": row.get("链接类型"),
        }

    def _record_source(
        self, link_id: str, row: dict[str, Any], batch_id: str, edge: dict[str, Any]
    ) -> None:
        """把一条原始关系边登记为链接出处，同批次同边只登记一次。"""
        prov = self._provenance.get(link_id)
        if prov is None:
            prov = {
                "链接ID": link_id,
                "源实体": row.get("源实体"),
                "外部URI": row.get("外部URI"),
                "链接类型": row.get("链接类型"),
                "delivered": False,
                "sources": [],
            }
            self._provenance[link_id] = prov
        already = any(
            s["batch_id"] == batch_id and s["edge_id"] == edge.get("edge_id")
            for s in prov["sources"]
        )
        if not already:
            prov["sources"].append({
                "batch_id": batch_id,
                "edge_id": edge.get("edge_id"),
                "source_entity": edge.get("source_entity"),
                "target_entity": edge.get("target_entity"),
                "evidence_page": edge.get("evidence_page"),
                "delivered": False,
            })
        # 新出处尚未回执，条目整体回到未交付，直到全部出处均可回查
        prov["delivered"] = all(s["delivered"] for s in prov["sources"])

    def _get_or_create_link(
        self, entity: str, batch_id: str, edge: dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        """按 (实体, 外部URI) 取唯一有效链接；不存在才创建，返回 (链接行, 是否新建)。"""
        identity = self._link_identity(entity)
        link_id = identity["链接ID"]
        external_uri = identity["外部URI"]
        rows = store.rows(self.MODULE)

        active = next(
            (
                row for row in rows
                if row.get("源实体") == entity
                and row.get("外部URI") == external_uri
                and row.get("status") in ACTIVE_STATUSES
            ),
            None,
        )
        if active is not None:
            row, created = active, False
        else:
            # 同一外链身份可能因失效/移除成为历史链接：重放时复用身份重新发布，
            # 不允许为同一实体与 URI 再写第二条
            historical = next(
                (row for row in rows if row.get("链接ID") == link_id), None
            )
            if historical is not None:
                row = historical
                row["外部URI"] = external_uri
                row["链接类型"] = identity["链接类型"]
                row["外部来源"] = "relation_graph_pipeline"
                row["验证状态"] = "自动生成"
                row["上次验证"] = _utc_now()
                row["status"] = "待验证"
                row["pending"] = True
                row["abnormal"] = False
                created = False
            else:
                row = {
                    "id": max((int(r.get("id", 0)) for r in rows), default=0) + 1,
                    "链接ID": link_id,
                    "源实体": entity,
                    "外部URI": external_uri,
                    "链接类型": identity["链接类型"],
                    "外部来源": "relation_graph_pipeline",
                    "验证状态": "自动生成",
                    "上次验证": _utc_now(),
                    "status": "待验证",
                    "pending": True,
                    "abnormal": False,
                }
                rows.append(row)
                created = True

        self._record_source(link_id, row, batch_id, edge)
        return row, created

    # ------------------------------------------------------------------
    # 关系图谱批次消费
    # ------------------------------------------------------------------
    def consume_relations(
        self,
        relation_graph_edges: list[dict[str, Any]],
        batch_id: str | None = None,
    ) -> dict[str, Any]:
        """消费关系图谱的已验证边，为实体建立外部知识链接。

        入参每条边至少含 edge_id / source_entity / target_entity / evidence_page。
        同批多条边共用实体时只生成一条外链，但每条边都会拿到自己的回执。
        """
        if not relation_graph_edges:
            return {
                "ok": True,
                "batch_id": batch_id,
                "input_edges": 0,
                "link_count": 0,
                "links": [],
                "receipts": [],
                "created_link_ids": [],
                "reused_link_ids": [],
            }

        if not batch_id:
            fingerprint = "|".join(
                str(edge.get("edge_id", "")) for edge in relation_graph_edges
            )
            batch_id = "adhoc-" + hashlib.sha1(
                fingerprint.encode("utf-8")
            ).hexdigest()[:12]

        with self._lock:
            receipts: list[dict[str, Any]] = []
            link_by_id: dict[str, dict[str, Any]] = {}
            created_link_ids: list[str] = []
            reused_link_ids: list[str] = []

            for edge in relation_graph_edges:
                edge_links: list[dict[str, Any]] = []
                for entity in (
                    edge.get("source_entity", ""),
                    edge.get("target_entity", ""),
                ):
                    if not entity:
                        continue
                    row, created = self._get_or_create_link(entity, batch_id, edge)
                    link_id = str(row["链接ID"])
                    if link_id not in link_by_id:
                        link_by_id[link_id] = row
                        (created_link_ids if created else reused_link_ids).append(link_id)
                    if not any(item["链接ID"] == link_id for item in edge_links):
                        edge_links.append(self._link_summary(row))

                receipts.append({
                    "batch_id": batch_id,
                    "edge_id": edge.get("edge_id"),
                    "source_entity": edge.get("source_entity"),
                    "target_entity": edge.get("target_entity"),
                    "evidence_page": edge.get("evidence_page"),
                    "links": edge_links,
                    "receipt_status": RECEIPT_PENDING,
                })

            unique_links = [self._link_summary(row) for row in link_by_id.values()]
            return {
                "ok": True,
                "batch_id": batch_id,
                "input_edges": len(relation_graph_edges),
                "link_count": len(unique_links),
                "links": unique_links,
                "receipts": receipts,
                "created_link_ids": created_link_ids,
                "reused_link_ids": reused_link_ids,
            }

    def mark_batch_delivered(self, batch_id: str) -> list[str]:
        """整批回执到齐后由关系图谱调用：核销该批次的全部出处，返回受影响链接ID。"""
        with self._lock:
            affected: list[str] = []
            for link_id, prov in self._provenance.items():
                touched = False
                for source in prov["sources"]:
                    if source["batch_id"] == batch_id and not source["delivered"]:
                        source["delivered"] = True
                        touched = True
                if touched:
                    # 条目级交付态：所有出处（含后续批次复用）都能回查才算已交付
                    prov["delivered"] = all(s["delivered"] for s in prov["sources"])
                    affected.append(link_id)
            return affected

    # ------------------------------------------------------------------
    # 出处台账查询：建边清单与已交付链接必须对得上
    # ------------------------------------------------------------------
    def list_provenance(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    **prov,
                    "交付状态": RECEIPT_DELIVERED if prov["delivered"] else RECEIPT_PENDING,
                    "source_count": len(prov["sources"]),
                }
                for prov in self._provenance.values()
            ]

    def get_provenance(self, link_id: str) -> dict[str, Any] | None:
        with self._lock:
            prov = self._provenance.get(link_id)
            if prov is None:
                return None
            return {
                **prov,
                "交付状态": RECEIPT_DELIVERED if prov["delivered"] else RECEIPT_PENDING,
                "source_count": len(prov["sources"]),
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
                    existing["上次验证"] = _utc_now()
                    validated += 1
        return {"ok": True, "pending_count": len(pending), "validated_count": validated}

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        base["link_stats"] = self.build_aggregate_input()
        with self._lock:
            base["provenance_ledger"] = {
                "tracked_links": len(self._provenance),
                "delivered": sum(1 for p in self._provenance.values() if p["delivered"]),
            }
        return base
