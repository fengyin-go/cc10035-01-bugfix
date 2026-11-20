"""关系图谱 -> 知识链接 跨模块流水线接口。

提供实体知识链接数据批次的提交、重放、出处回执与台账查询，
路由独立于通用资源路由，便于按业务语义做跨服务编排。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services import service_for
from app.services.knowledge_link import KnowledgeLinkService
from app.services.relation_graph import RelationGraphService

router = APIRouter(prefix="/api/pipeline/relation-graph", tags=["知识链接批次流水线"])


class BatchSubmitPayload(BaseModel):
    relation_ids: list[str] = Field(default_factory=list)


class ValidateEdgesPayload(BaseModel):
    edge_ids: list[str] = Field(default_factory=list)


class ReceiptPayload(BaseModel):
    edge_ids: list[str] | None = None


def relation_service() -> RelationGraphService:
    service = service_for("relation_graph")
    assert isinstance(service, RelationGraphService)
    return service


def knowledge_service() -> KnowledgeLinkService:
    service = service_for("knowledge_link")
    assert isinstance(service, KnowledgeLinkService)
    return service


@router.post("/validate-edges")
def validate_edges(payload: ValidateEdgesPayload) -> dict[str, Any]:
    """批量验证关系边，使其获得知识链接批次的准入资格。"""
    if not payload.edge_ids:
        raise HTTPException(status_code=400, detail="edge_ids 不能为空")
    return relation_service().validate_edges(payload.edge_ids)


@router.post("/knowledge-link-batches")
def submit_batch(payload: BatchSubmitPayload) -> dict[str, Any]:
    """提交（或重放）一批已验证关系边，生成实体知识链接数据批次。"""
    if not payload.relation_ids:
        raise HTTPException(status_code=400, detail="relation_ids 不能为空")
    return relation_service().submit_knowledge_link_batch(payload.relation_ids)


@router.get("/knowledge-link-batches")
def list_batches() -> dict[str, Any]:
    """列出实体知识链接数据批次台账。"""
    batches = relation_service().list_knowledge_link_batches()
    return {"ok": True, "total": len(batches), "batches": batches}


@router.get("/knowledge-link-batches/{batch_id}")
def get_batch(batch_id: str) -> dict[str, Any]:
    """查看批次中每条边的出处回执（建边清单）。"""
    detail = relation_service().get_knowledge_link_batch(batch_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"批次 {batch_id} 不存在")
    return {"ok": True, **detail}


@router.post("/knowledge-link-batches/{batch_id}/receipts")
def acknowledge(batch_id: str, payload: ReceiptPayload) -> dict[str, Any]:
    """逐条回传边的出处回执；覆盖整批后批次核销、链接条目转为已交付。"""
    result = relation_service().acknowledge_receipts(batch_id, payload.edge_ids)
    if not result["ok"]:
        raise HTTPException(status_code=404, detail=result["message"])
    return result


@router.get("/link-provenance")
def list_provenance() -> dict[str, Any]:
    """已交付/待交付链接的出处台账：链接身份与各条原始边一一可回查。"""
    items = knowledge_service().list_provenance()
    return {"ok": True, "total": len(items), "items": items}


@router.get("/link-provenance/{link_id}")
def get_provenance(link_id: str) -> dict[str, Any]:
    prov = knowledge_service().get_provenance(link_id)
    if prov is None:
        raise HTTPException(status_code=404, detail=f"链接 {link_id} 暂无出处台账")
    return {"ok": True, "provenance": prov}
