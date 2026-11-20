"""关系图谱领域服务：构建实体语义关系图。

关系图谱与知识链接模块之间的批次流水线在这里编排：

- 提交已验证关系边 -> 知识链接按实体去重生成外链（同批共端点只生成一条），
  边 ID 与来源页随批传递，外链可按原关系回查；
- 关系图谱持有实体知识链接数据批次与建边清单，逐条登记每条边的证据去向回执；
- 图谱只有拿到覆盖整批的出处回执才能核销批次；中断后值班员重投同一批边，
  批次 ID 由边集合确定性派生，重放幂等且复用第一次生成的外链身份。
"""
from __future__ import annotations

import hashlib
import threading
from typing import Any

from app.services.base import BaseModuleService
from app.services.knowledge_link import RECEIPT_DELIVERED
from app.store import store


RELATION_TYPES = [
    "从属关系", "引用关系", "时序关系", "同义关系",
    "因果关系", "对比关系", "并列关系", "层级关系",
]

BATCH_PENDING = "待回执"
BATCH_SETTLED = "已核销"


class RelationGraphService(BaseModuleService):
    MODULE = "relation_graph"
    ENTITY = "关系边"
    OPERATION_TERM = "关联"
    LIST_FIELDS = [
        "边ID", "源实体", "目标实体", "关系类型", "抽取页面", "原文证据",
        "关联强度", "证据去向",
    ]
    REQUIRED_FIELDS = ["边ID", "源实体", "目标实体"]
    STATUS_ORDER = ["已抽取", "已验证", "已入库", "已过期"]
    ACTION_RULES = {"验证关系": "已抽取", "确认入库": "已验证", "标记过期": "已抽取"}
    NEGATIVE_ACTIONS = []

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # 实体知识链接数据批次：batch_id -> 批次记录
        self._batches: dict[str, dict[str, Any]] = {}
        # 建边清单：batch_id -> {边ID: 该边的证据去向登记}
        self._edge_ledger: dict[str, dict[str, dict[str, Any]]] = {}

    def build_relations(self, entity_extract_records: list[dict[str, Any]]) -> dict[str, Any]:
        """基于实体抽取记录构建关系图谱。消费 entity_extract 输出。"""
        if len(entity_extract_records) < 2:
            return {"ok": True, "relation_count": 0, "relations": [], "reason": "实体数量不足"}

        relations = []
        for i in range(len(entity_extract_records)):
            for j in range(i + 1, min(i + 4, len(entity_extract_records))):
                src = entity_extract_records[i]
                tgt = entity_extract_records[j]
                if src.get("页面来源") != tgt.get("页面来源"):
                    continue
                rel_type_idx = (i * 7 + j * 13) % len(RELATION_TYPES)
                edge_id = hashlib.md5(
                    f"{src.get('实体ID')}|{tgt.get('实体ID')}|{RELATION_TYPES[rel_type_idx]}".encode()
                ).hexdigest()[:10]
                strength = round(0.85 - (abs(i - j) * 0.08), 2)
                relations.append({
                    "边ID": edge_id,
                    "源实体": src.get("实体名称", src.get("实体ID", "")),
                    "目标实体": tgt.get("实体名称", tgt.get("实体ID", "")),
                    "关系类型": RELATION_TYPES[rel_type_idx],
                    "抽取页面": src.get("页面来源"),
                    "原文证据": f"从{src.get('页面来源')}中抽取的共现关系",
                    "关联强度": str(strength),
                })

        return {
            "ok": True,
            "entity_count": len(entity_extract_records),
            "relation_count": len(relations),
            "relations": relations,
            "relation_types_used": list(set(r["关系类型"] for r in relations)),
        }

    def feed_knowledge_link(self, relation_ids: list[str]) -> dict[str, Any]:
        """将已验证的关系提供给知识链接模块（边 ID 与来源页一并交付）。"""
        relations = []
        for rid in relation_ids:
            row = next((r for r in store.rows(self.MODULE) if r.get("边ID") == rid), None)
            if row and row.get("status") == "已验证":
                relations.append({
                    "edge_id": rid,
                    "source_entity": row.get("源实体"),
                    "target_entity": row.get("目标实体"),
                    "relation_type": row.get("关系类型"),
                    "evidence_page": row.get("抽取页面"),
                })
        return {
            "ok": True,
            "requested_ids": len(relation_ids),
            "found_relations": len(relations),
            "relations": relations,
        }

    # ------------------------------------------------------------------
    # 实体知识链接数据批次流水线
    # ------------------------------------------------------------------
    @staticmethod
    def _batch_id_for(edge_ids: list[str]) -> str:
        """批次身份由整批边集合确定性派生：重投同一批边永远落到同一批次。"""
        fingerprint = ",".join(sorted(edge_ids))
        return "klb-" + hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:12]

    def _edge_row(self, edge_id: str) -> dict[str, Any] | None:
        return next(
            (row for row in store.rows(self.MODULE) if row.get("边ID") == edge_id),
            None,
        )

    def validate_edges(self, edge_ids: list[str]) -> dict[str, Any]:
        """把已抽取的关系边验证为「已验证」，获得知识链接批次的准入资格。

        共享动作骨架的状态机为全部模块的既有约定，这里提供领域级的批量验证入口，
        避免值班员逐条流转，也让批次流水线的准入状态显式可查。
        """
        validated: list[str] = []
        unknown: list[str] = []
        skipped: list[str] = []
        with self._lock:
            for edge_id in edge_ids:
                row = self._edge_row(edge_id)
                if row is None:
                    unknown.append(edge_id)
                    continue
                if row.get("status") == "已验证":
                    skipped.append(edge_id)
                    continue
                if row.get("status") != "已抽取":
                    skipped.append(edge_id)
                    continue
                row["status"] = "已验证"
                validated.append(edge_id)
        return {
            "ok": True,
            "validated_edge_ids": validated,
            "skipped_edge_ids": skipped,
            "unknown_edge_ids": unknown,
        }

    def _write_edge_destinations(
        self, batch_id: str, receipts: list[dict[str, Any]]
    ) -> None:
        """把每条边的证据去向回写到关系边处理清单。"""
        for receipt in receipts:
            edge_id = receipt.get("edge_id")
            row = self._edge_row(str(edge_id))
            if row is None:
                continue
            link_ids = [str(item["链接ID"]) for item in receipt.get("links", [])]
            batch = self._batches[batch_id]
            destination = (
                f"{batch['状态']}批次 {batch_id}："
                + "、".join(link_ids)
                if link_ids
                else f"{batch['状态']}批次 {batch_id}：无外链"
            )
            row["证据去向"] = destination

    def submit_knowledge_link_batch(self, relation_ids: list[str]) -> dict[str, Any]:
        """从关系图谱入口提交一批已验证边，生成实体知识链接数据批次。

        幂等：同一批边重放返回同一批次与同批回执，复用首次生成的外链身份，
        不会重复写链接；已核销批次重放原样返回台账。
        """
        from app.services import service_for

        # 入参去重但保留首次出现顺序，重复投递同一个边 ID 不产生重复登记
        seen: set[str] = set()
        ordered_ids = [rid for rid in relation_ids if not (rid in seen or seen.add(rid))]
        batch_id = self._batch_id_for(ordered_ids)

        # 固定加锁顺序：先图谱后知识链接，避免并发流水线交叉加锁死锁
        with self._lock:
            existing = self._batches.get(batch_id)
            if existing is not None:
                return {
                    "ok": True,
                    "batch_id": batch_id,
                    "replayed": True,
                    "batch": self._public_batch(existing),
                    "receipts": list(self._edge_ledger[batch_id].values()),
                    "message": "批次已存在，按首次生成的外链身份返回出处回执",
                }

            feed = self.feed_knowledge_link(ordered_ids)
            edges = feed["relations"]
            rejected = [
                rid for rid in ordered_ids
                if rid not in {edge["edge_id"] for edge in edges}
            ]

            # 没有任何已验证边时不产生批次：来源无法回查的条目不允许进入交付流程
            if not edges:
                return {
                    "ok": True,
                    "batch_id": None,
                    "replayed": False,
                    "batch": None,
                    "receipts": [],
                    "rejected_edge_ids": rejected,
                    "message": "没有可入批的已验证关系边，未生成知识链接批次",
                }

            knowledge = service_for("knowledge_link")
            result = knowledge.consume_relations(edges, batch_id=batch_id)
            receipts = result["receipts"]

            ledger: dict[str, dict[str, Any]] = {}
            for receipt in receipts:
                entry = dict(receipt)
                entry["acknowledged"] = False
                ledger[str(receipt["edge_id"])] = entry

            batch = {
                "batch_id": batch_id,
                "edge_ids": [edge["edge_id"] for edge in edges],
                "rejected_edge_ids": rejected,
                "状态": BATCH_PENDING,
                "receipt_count": len(receipts),
                "acknowledged_count": 0,
                "link_ids": [link["链接ID"] for link in result["links"]],
                "created_link_ids": result["created_link_ids"],
                "reused_link_ids": result["reused_link_ids"],
            }
            self._batches[batch_id] = batch
            self._edge_ledger[batch_id] = ledger
            self._write_edge_destinations(batch_id, receipts)

            return {
                "ok": True,
                "batch_id": batch_id,
                "replayed": False,
                "batch": self._public_batch(batch),
                "receipts": list(ledger.values()),
                "rejected_edge_ids": rejected,
                "message": "实体知识链接数据批次已生成，等待逐条出处回执",
            }

    def acknowledge_receipts(
        self, batch_id: str, edge_ids: list[str] | None = None
    ) -> dict[str, Any]:
        """逐条登记边的出处回执；不传边 ID 时回执该批次剩余全部边。

        图谱只有拿到覆盖整批的出处回执才能核销批次，核销同时通知知识链接
        把对应条目标记为已交付。
        """
        from app.services import service_for

        with self._lock:
            batch = self._batches.get(batch_id)
            if batch is None:
                return {"ok": False, "message": f"批次 {batch_id} 不存在"}
            ledger = self._edge_ledger[batch_id]

            targets = list(ledger.keys()) if not edge_ids else [
                str(edge_id) for edge_id in edge_ids
            ]
            unknown = [edge_id for edge_id in targets if edge_id not in ledger]
            updated: list[str] = []
            for edge_id in targets:
                entry = ledger.get(edge_id)
                if entry is not None and not entry["acknowledged"]:
                    entry["acknowledged"] = True
                    entry["receipt_status"] = RECEIPT_DELIVERED
                    updated.append(edge_id)

            acknowledged_count = sum(1 for entry in ledger.values() if entry["acknowledged"])
            batch["acknowledged_count"] = acknowledged_count

            settled = False
            if acknowledged_count == len(ledger):
                if batch["状态"] != BATCH_SETTLED:
                    settled = True
                batch["状态"] = BATCH_SETTLED
                knowledge = service_for("knowledge_link")
                knowledge.mark_batch_delivered(batch_id)
                # 证据去向回写为最终核销状态
                self._write_edge_destinations(
                    batch_id, list(ledger.values())
                )
            else:
                batch["状态"] = BATCH_PENDING
                self._write_edge_destinations(batch_id, list(ledger.values()))

            return {
                "ok": True,
                "batch_id": batch_id,
                "updated_edge_ids": updated,
                "unknown_edge_ids": unknown,
                "acknowledged_count": acknowledged_count,
                "receipt_count": len(ledger),
                "batch_status": batch["状态"],
                "settled": settled or batch["状态"] == BATCH_SETTLED,
                "receipts": list(ledger.values()),
                "message": (
                    "整批出处回执已到齐，批次核销，知识链接条目已交付"
                    if batch["状态"] == BATCH_SETTLED
                    else "回执已登记，仍有边未回执行，批次保持待回执"
                ),
            }

    def list_knowledge_link_batches(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._public_batch(batch) for batch in self._batches.values()]

    def get_knowledge_link_batch(self, batch_id: str) -> dict[str, Any] | None:
        with self._lock:
            batch = self._batches.get(batch_id)
            if batch is None:
                return None
            return {
                "batch": self._public_batch(batch),
                "receipts": list(self._edge_ledger[batch_id].values()),
            }

    @staticmethod
    def _public_batch(batch: dict[str, Any]) -> dict[str, Any]:
        return {
            "batch_id": batch["batch_id"],
            "edge_ids": list(batch["edge_ids"]),
            "rejected_edge_ids": list(batch["rejected_edge_ids"]),
            "状态": batch["状态"],
            "receipt_count": batch["receipt_count"],
            "acknowledged_count": batch["acknowledged_count"],
            "link_ids": list(batch["link_ids"]),
            "created_link_ids": list(batch["created_link_ids"]),
            "reused_link_ids": list(batch["reused_link_ids"]),
        }

    def compute_graph_stats(self) -> dict[str, Any]:
        """图谱统计。"""
        rows = store.rows(self.MODULE)
        entities: set[str] = set()
        rel_type_dist: dict[str, int] = {}
        for row in rows:
            entities.add(str(row.get("源实体", "")))
            entities.add(str(row.get("目标实体", "")))
            rtype = row.get("关系类型", "未知")
            rel_type_dist[rtype] = rel_type_dist.get(rtype, 0) + 1
        with self._lock:
            batch_stats = {
                "total_batches": len(self._batches),
                "pending_batches": sum(
                    1 for b in self._batches.values() if b["状态"] == BATCH_PENDING
                ),
                "settled_batches": sum(
                    1 for b in self._batches.values() if b["状态"] == BATCH_SETTLED
                ),
            }
        return {
            "total_relations": len(rows),
            "unique_entities": len(entities) - (1 if "" in entities else 0),
            "relation_type_distribution": rel_type_dist,
            "validated": sum(1 for r in rows if r.get("status") == "已验证"),
            "ingested": sum(1 for r in rows if r.get("status") == "已入库"),
            "knowledge_link_batches": batch_stats,
        }

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        base["graph_stats"] = self.compute_graph_stats()
        return base
