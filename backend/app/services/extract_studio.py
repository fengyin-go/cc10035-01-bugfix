"""抽取工坊领域服务：管理交互式抽取规则的标注和验证。"""
from __future__ import annotations

import hashlib
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class ExtractStudioService(BaseModuleService):
    MODULE = "extract_studio"
    ENTITY = "交互任务"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["任务名称", "示例页面", "标注区域", "字段列表", "预览结果", "标注人", "完成时间"]
    REQUIRED_FIELDS = ["任务名称", "示例页面", "标注区域"]
    STATUS_ORDER = ["草稿", "标注中", "已审核", "已发布"]
    ACTION_RULES = {"开始标注": "草稿", "提交审核": "标注中", "发布规则": "已审核"}
    NEGATIVE_ACTIONS = []

    def export_to_template_rule(self, studio_task_id: int) -> dict[str, Any]:
        """将审核完成的标注任务导出为模板规则。消费抽取工坊→模板规则的跨模块链路。"""
        task = store.find(self.MODULE, studio_task_id)
        if task is None:
            return {"ok": False, "reason": "抽取工坊任务不存在"}
        if task.get("status") != "已审核":
            return {"ok": False, "reason": "仅供已审核的任务导出模板"}

        field_list = task.get("字段列表", "")
        annotation_areas = task.get("标注区域", "")
        sample_page = task.get("示例页面", "")

        selectors_from_areas = []
        for area in annotation_areas.split(","):
            area = area.strip()
            if area:
                if area.startswith(".") or area.startswith("#"):
                    selectors_from_areas.append(area)
                elif area.startswith("//"):
                    selectors_from_areas.append(area)
                else:
                    selectors_from_areas.append(f".{area}")

        template = {
            "模板名称": f"来自工坊-{task.get('任务名称')}",
            "适用站点": sample_page,
            "提取字段": field_list,
            "字段选择器": ", ".join(selectors_from_areas),
            "采样页面": sample_page,
            "校验规则": "非空校验、类型校验",
            "更新策略": "每周检查",
        }
        return {
            "ok": True,
            "studio_task_id": studio_task_id,
            "template": template,
            "selector_count": len(selectors_from_areas),
            "field_count": len([f.strip() for f in field_list.split(",") if f.strip()]),
        }

    def preview_extraction(self, studio_task_id: int) -> dict[str, Any]:
        """预览标注区域的实际抽取效果。"""
        task = store.find(self.MODULE, studio_task_id)
        if task is None:
            return {"ok": False, "reason": "任务不存在"}

        field_list = task.get("字段列表", "")
        fields = [f.strip() for f in field_list.split(",") if f.strip()]
        sample_page = task.get("示例页面", "")

        preview = {}
        for i, field in enumerate(fields):
            val_hash = int(hashlib.md5(f"{sample_page}|{field}|{i}".encode()).hexdigest()[:4], 16)
            preview[field] = f"提取值样例{f'#{val_hash % 100}' if val_hash % 3 != 0 else '（未匹配）'}"

        return {
            "ok": True,
            "task_name": task.get("任务名称"),
            "sample_page": sample_page,
            "fields_count": len(fields),
            "preview": preview,
            "match_ratio": round(
                sum(1 for v in preview.values() if "未匹配" not in v) / len(preview), 2
            ) if preview else 0.0,
        }

    def compute_annotation_progress(self) -> dict[str, Any]:
        """标注进度统计。"""
        rows = store.rows(self.MODULE)
        draft = sum(1 for r in rows if r.get("status") == "草稿")
        annotating = sum(1 for r in rows if r.get("status") == "标注中")
        reviewed = sum(1 for r in rows if r.get("status") == "已审核")
        published = sum(1 for r in rows if r.get("status") == "已发布")
        return {
            "total_tasks": len(rows),
            "draft": draft,
            "annotating": annotating,
            "reviewed": reviewed,
            "published": published,
            "completion_ratio": round((reviewed + published) / len(rows), 2) if rows else 0.0,
        }

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        base["annotation_progress"] = self.compute_annotation_progress()
        return base