"""Schema对齐领域服务：统一不同数据源的Schema到标准数据模型。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class SchemaAlignService(BaseModuleService):
    MODULE = "schema_align"
    ENTITY = "对齐规则"
    OPERATION_TERM = "转换"
    LIST_FIELDS = ["规则名称", "源Schema", "目标Schema", "字段映射", "值转换", "默认填充", "冲突处理"]
    REQUIRED_FIELDS = ["规则名称", "源Schema", "目标Schema"]
    STATUS_ORDER = ["草稿", "已启用", "已停用"]
    ACTION_RULES = {"启用规则": "草稿", "停用规则": "已启用"}
    NEGATIVE_ACTIONS = ["停用规则"]

    def parse_field_mapping(self, mapping_str: str) -> dict[str, str]:
        """解析字段映射字符串 'src1->tgt1, src2->tgt2' 为字典。"""
        pairs = [p.strip() for p in mapping_str.split(",") if "->" in p]
        result: dict[str, str] = {}
        for pair in pairs:
            parts = pair.split("->", 1)
            if len(parts) == 2:
                result[parts[0].strip()] = parts[1].strip()
        return result

    def align_entities(self, entity_extract_records: list[dict[str, Any]],
                       rule_id: int) -> dict[str, Any]:
        """将实体抽取结果按对齐规则映射到目标Schema，被输出目标模块消费。"""
        rule = store.find(self.MODULE, rule_id)
        if rule is None:
            return {"ok": False, "reason": "对齐规则不存在"}
        mapping = self.parse_field_mapping(rule.get("字段映射", ""))
        source_schema = [s.strip() for s in rule.get("源Schema", "").split(",") if s.strip()]
        target_schema = [s.strip() for s in rule.get("目标Schema", "").split(",") if s.strip()]
        defaults = rule.get("默认填充", "")
        default_map = {}
        if defaults:
            for item in defaults.split(","):
                if ":" in item:
                    k, v = item.split(":", 1)
                    default_map[k.strip()] = v.strip()

        aligned = []
        for rec in entity_extract_records:
            target: dict[str, Any] = {}
            for t_field in target_schema:
                src_field = None
                for s, t in mapping.items():
                    if t == t_field:
                        src_field = s
                        break
                if src_field and src_field in rec:
                    target[t_field] = rec[src_field]
                elif t_field in default_map:
                    target[t_field] = default_map[t_field]
                else:
                    target[t_field] = ""
            aligned.append(target)

        return {
            "ok": True,
            "rule_name": rule.get("规则名称"),
            "source_schema": source_schema,
            "target_schema": target_schema,
            "input_count": len(entity_extract_records),
            "output_count": len(aligned),
            "aligned_records": aligned,
        }

    def build_output_schema(self, rule_id: int) -> dict[str, Any]:
        """构建输出目标的Schema定义，供 output_target 模块消费。"""
        rule = store.find(self.MODULE, rule_id)
        if rule is None:
            return {"ok": False, "reason": "规则不存在"}
        target_fields = [s.strip() for s in rule.get("目标Schema", "").split(",") if s.strip()]
        mapping = self.parse_field_mapping(rule.get("字段映射", ""))
        return {
            "ok": True,
            "rule_id": rule_id,
            "target_fields": target_fields,
            "field_count": len(target_fields),
            "mapping_rules": mapping,
            "value_transform": rule.get("值转换", ""),
            "conflict_handler": rule.get("冲突处理", "忽略"),
        }

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        rows = store.rows(self.MODULE)
        base["alignment_stats"] = {
            "total_rules": len(rows),
            "active_rules": sum(1 for r in rows if r.get("status") == "已启用"),
            "with_mapping": sum(1 for r in rows if r.get("字段映射", "").strip()),
        }
        return base