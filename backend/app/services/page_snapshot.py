"""页面快照领域服务：保存和比对页面状态快照。"""
from __future__ import annotations

import hashlib
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class PageSnapshotService(BaseModuleService):
    MODULE = "page_snapshot"
    ENTITY = "快照记录"
    OPERATION_TERM = "采集"
    LIST_FIELDS = ["快照ID", "页面URI", "DOM哈希", "文本哈希", "抓取时间", "页面大小", "是否变更"]
    REQUIRED_FIELDS = ["快照ID", "页面URI", "DOM哈希"]
    STATUS_ORDER = ["已保存", "已比对", "已过期"]
    ACTION_RULES = {"执行比对": "已保存", "标记过期": "已比对"}
    NEGATIVE_ACTIONS = []

    def compare_snapshots(self, snapshot_id: int) -> dict[str, Any]:
        """比对两次快照变化，供质量检查和探针选择联动。"""
        snapshot = store.find(self.MODULE, snapshot_id)
        if snapshot is None:
            return {"ok": False, "reason": "快照不存在"}
        all_snaps = [
            r for r in store.rows(self.MODULE)
            if r.get("页面URI") == snapshot.get("页面URI")
        ]
        all_snaps.sort(key=lambda r: r.get("id", 0))
        current_dom = snapshot.get("DOM哈希", "")
        current_text = snapshot.get("文本哈希", "")

        prev_dom = ""
        prev_text = ""
        if len(all_snaps) >= 2:
            prev_idx = all_snaps.index(snapshot) - 1
            if prev_idx >= 0:
                prev = all_snaps[prev_idx]
                prev_dom = prev.get("DOM哈希", "")
                prev_text = prev.get("文本哈希", "")

        dom_changed = current_dom != prev_dom
        text_changed = current_text != prev_text

        return {
            "ok": True,
            "snapshot_id": snapshot_id,
            "page_uri": snapshot.get("页面URI"),
            "dom_changed": dom_changed,
            "text_changed": text_changed,
            "change_count": (1 if dom_changed else 0) + (1 if text_changed else 0),
            "current_dom_hash": current_dom,
            "previous_dom_hash": prev_dom,
        }

    def feed_quality_check(self, snapshot_id: int) -> dict[str, Any]:
        """将快照比对结果提供给质量检查。"""
        compare_result = self.compare_snapshots(snapshot_id)
        if not compare_result["ok"]:
            return compare_result

        return {
            "ok": True,
            "snapshot_id": snapshot_id,
            "page_uri": compare_result["page_uri"],
            "has_changes": compare_result["change_count"] > 0,
            "check_items": [
                {"name": "DOM一致性", "passed": not compare_result["dom_changed"], "detail": "DOM哈希比对"},
                {"name": "文本一致性", "passed": not compare_result["text_changed"], "detail": "文本哈希比对"},
            ],
            "recommend_rule_update": compare_result["dom_changed"],
        }

    def compute_staleness(self) -> dict[str, Any]:
        """计算快照过时率。"""
        rows = store.rows(self.MODULE)
        total = len(rows)
        expired = sum(1 for r in rows if r.get("status") == "已过期")
        changed = sum(1 for r in rows if r.get("是否变更") == "是" or r.get("是否变更") == "true")
        return {
            "total_snapshots": total,
            "expired_count": expired,
            "changed_count": changed,
            "staleness_ratio": round(expired / total, 2) if total > 0 else 0.0,
        }

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        base["staleness"] = self.compute_staleness()
        return base