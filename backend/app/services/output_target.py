"""输出目标领域服务：管理结构化数据的输出分发配置。"""
from __future__ import annotations

import json
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


OUTPUT_FORMATS = {"json": "application/json", "csv": "text/csv",
                   "parquet": "application/x-parquet", "avro": "application/avro"}


class OutputTargetService(BaseModuleService):
    MODULE = "output_target"
    ENTITY = "输出配置"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["配置名称", "输出类型", "目标地址", "输出格式", "分区策略", "刷新频率", "校验方式"]
    REQUIRED_FIELDS = ["配置名称", "输出类型", "目标地址"]
    STATUS_ORDER = ["草稿", "已启用", "已暂停"]
    ACTION_RULES = {"启用配置": "草稿", "暂停配置": "已启用"}
    NEGATIVE_ACTIONS = []

    def format_entity_output(self, entities: list[dict[str, Any]],
                             config_id: int) -> dict[str, Any]:
        """将实体对齐后的数据按输出配置格式化。消费 schema_align 输出。"""
        config = store.find(self.MODULE, config_id)
        if config is None:
            return {"ok": False, "reason": "输出配置不存在"}
        output_type = config.get("输出类型", "json")
        output_format = config.get("输出格式", "json")
        target = config.get("目标地址", "")

        if output_type == "json":
            payload = json.dumps(entities, ensure_ascii=False, indent=2)
            mime = OUTPUT_FORMATS.get(output_format, "application/json")
        elif output_type == "csv":
            if entities:
                keys = list(entities[0].keys())
                lines = [",".join(keys)]
                for e in entities:
                    lines.append(",".join(str(e.get(k, "")) for k in keys))
                payload = "\n".join(lines)
            else:
                payload = ""
            mime = OUTPUT_FORMATS.get("csv", "text/csv")
        else:
            payload = json.dumps(entities, ensure_ascii=False)
            mime = OUTPUT_FORMATS.get(output_format, "application/json")

        return {
            "ok": True,
            "config_name": config.get("配置名称"),
            "output_type": output_type,
            "target": target,
            "mime_type": mime,
            "record_count": len(entities),
            "payload_size": len(payload.encode("utf-8")),
            "payload_sample": payload[:200] if len(payload) > 200 else payload,
        }

    def build_output_chain(self, entity_extract_count: int,
                           schema_align_rule_id: int) -> dict[str, Any]:
        """构建从实体抽取到输出的完整链路的摘要信息。"""
        rule = store.find("schema_align", schema_align_rule_id)
        rows = store.rows(self.MODULE)
        active_outputs = [r for r in rows if r.get("status") == "已启用"]
        return {
            "entity_count": entity_extract_count,
            "align_rule": rule.get("规则名称") if rule else "未知",
            "active_output_targets": len(active_outputs),
            "target_types": [r.get("输出类型") for r in active_outputs],
            "output_chain_ready": len(active_outputs) > 0,
        }

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        rows = store.rows(self.MODULE)
        base["output_stats"] = {
            "total_configs": len(rows),
            "formats": {r.get("输出格式"): 0 for r in rows},
            "active_targets": sum(1 for r in rows if r.get("status") == "已启用"),
        }
        return base