"""抽取指标领域服务：追踪采集抽取的核心效率指标。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class ExtractMetricService(BaseModuleService):
    MODULE = "extract_metric"
    ENTITY = "指标记录"
    OPERATION_TERM = "采集"
    LIST_FIELDS = ["记录时间", "所属任务", "成功页面数", "失败页面数", "抽取字段数", "实体数", "平均耗时"]
    REQUIRED_FIELDS = ["记录时间", "所属任务", "成功页面数"]
    STATUS_ORDER = ["记录中", "已聚合", "已归档"]
    ACTION_RULES = {"聚合指标": "记录中", "归档记录": "已聚合"}
    NEGATIVE_ACTIONS = []

    def collect_from_classifier(self) -> dict[str, Any]:
        """从内容分类模块消费数据生成效率指标。"""
        from app.services.content_classifier import ContentClassifierService
        ccs = ContentClassifierService()
        stats = ccs.compute_classification_stats()
        feed = ccs.feed_extract_metric()
        return {
            "ok": True,
            "source_module": "content_classifier",
            "classified_pages": stats["total_labels"],
            "category_count": len(stats["category_distribution"]),
            "review_ratio": stats["review_ratio"],
            "metric_derived": feed,
        }

    def collect_from_probe_selector(self) -> dict[str, Any]:
        """从探针选择模块消费数据。"""
        from app.services.probe_selector import ProbeSelectorService
        pss = ProbeSelectorService()
        health = pss.compute_probe_health()
        return {
            "ok": True,
            "source_module": "probe_selector",
            "total_probes": health["total_probes"],
            "health_ratio": health["health_ratio"],
            "valid_probes": health["valid_probes"],
        }

    def collect_from_template(self) -> dict[str, Any]:
        """从模板规则模块消费数据。"""
        from app.services.template_rule import TemplateRuleService
        trs = TemplateRuleService()
        coverage = trs.compute_template_coverage()
        return {
            "ok": True,
            "source_module": "template_rule",
            "template_count": coverage["template_count"],
            "site_count": coverage["site_count"],
            "total_selectors": coverage["total_selectors"],
        }

    def aggregate_all_metrics(self) -> dict[str, Any]:
        """跨模块聚合所有采集指标。"""
        classifier_metrics = self.collect_from_classifier()
        probe_metrics = self.collect_from_probe_selector()
        template_metrics = self.collect_from_template()

        return {
            "ok": True,
            "sources": ["content_classifier", "probe_selector", "template_rule"],
            "classified_pages": classifier_metrics.get("classified_pages", 0),
            "probe_health_ratio": probe_metrics.get("health_ratio", 0.0),
            "template_coverage_sites": template_metrics.get("site_count", 0),
            "summary": (
                f"内容分类{classifier_metrics.get('classified_pages', 0)}页, "
                f"探针健康度{probe_metrics.get('health_ratio', 0.0)}, "
                f"模板覆盖{template_metrics.get('site_count', 0)}站"
            ),
        }

    def compute_metrics_summary(self) -> dict[str, Any]:
        """直接统计指标摘要。"""
        rows = store.rows(self.MODULE)
        total_success = 0
        total_fail = 0
        total_entities = 0
        for row in rows:
            try:
                total_success += int(row.get("成功页面数", "0"))
            except (ValueError, TypeError):
                pass
            try:
                total_fail += int(row.get("失败页面数", "0"))
            except (ValueError, TypeError):
                pass
            try:
                total_entities += int(row.get("实体数", "0"))
            except (ValueError, TypeError):
                pass
        total_pages = total_success + total_fail
        success_rate = round(total_success / total_pages, 2) if total_pages > 0 else 0.0
        return {
            "total_records": len(rows),
            "total_success_pages": total_success,
            "total_fail_pages": total_fail,
            "total_entities": total_entities,
            "overall_success_rate": success_rate,
        }

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        base["metrics_summary"] = self.compute_metrics_summary()
        return base