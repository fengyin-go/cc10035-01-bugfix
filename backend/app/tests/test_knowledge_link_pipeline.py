"""关系图谱 -> 知识链接批次流水线的领域测试。

只依赖标准库 unittest，运行：
    cd backend && .venv/bin/python -m unittest app.tests.test_knowledge_link_pipeline -v

覆盖缺陷场景：共端点边重复外链、出处丢失、中断重放幂等、并发去重、
整批回执才核销、来源不可回查条目不交付、历史链接身份复用。
"""
from __future__ import annotations

import concurrent.futures
import unittest

from app.seed import SEED_ROWS
from app.services import service_for
from app.services.knowledge_link import RECEIPT_DELIVERED, RECEIPT_PENDING
from app.store import store


class KnowledgeLinkPipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rg = service_for("relation_graph")
        cls.kl = service_for("knowledge_link")

    def setUp(self) -> None:
        # 每个用例从干净的种子数据与空台账开始
        for module, rows in store._tables.items():
            rows.clear()
            rows.extend(dict(row) for row in SEED_ROWS[module])
        self.rg._batches.clear()
        self.rg._edge_ledger.clear()
        self.kl._provenance.clear()

    def _edge(self, edge_id: str, src: str, tgt: str, page: str) -> str:
        entry, missing = self.rg.create_entry({
            "边ID": edge_id, "源实体": src, "目标实体": tgt,
            "关系类型": "引用关系", "抽取页面": page,
            "原文证据": f"{page} 共现", "关联强度": "0.8",
        })
        self.assertFalse(missing)
        entry["status"] = "已验证"
        return edge_id

    def _active_links(self) -> list[dict]:
        return [
            row for row in store.rows("knowledge_link")
            if row.get("status") in ("待验证", "已验证")
        ]

    def test_shared_endpoint_dedupes_external_links_and_keeps_provenance(self) -> None:
        e1 = self._edge("edge-ab", "实体A", "实体B", "https://news.example.com/p1")
        e2 = self._edge("edge-ac", "实体A", "实体C", "https://news.example.com/p1")

        result = self.rg.submit_knowledge_link_batch([e1, e2])

        self.assertEqual(2, len(result["receipts"]))
        # A→B、A→C 共端点 A：旧实现产出 4 条，去重后仅 3 条
        self.assertEqual(3, len(result["batch"]["link_ids"]))
        a_rows = [r for r in self._active_links() if r["源实体"] == "实体A"]
        self.assertEqual(1, len(a_rows))
        # 同一 (实体, URI) 全局唯一
        self.assertEqual(
            len({(r["源实体"], r["外部URI"]) for r in self._active_links()}),
            len(self._active_links()),
        )
        # 去重不丢出处：A 的外链可回查两条原始边及来源页
        prov = self.kl.get_provenance(a_rows[0]["链接ID"])
        self.assertEqual({e1, e2}, {s["edge_id"] for s in prov["sources"]})
        self.assertTrue(all(s["evidence_page"] for s in prov["sources"]))
        # 完整回执前条目不得当作已交付
        self.assertEqual(RECEIPT_PENDING, prov["交付状态"])

    def test_partial_receipt_cannot_settle_batch(self) -> None:
        e1 = self._edge("edge-ab", "实体A", "实体B", "https://news.example.com/p1")
        e2 = self._edge("edge-ac", "实体A", "实体C", "https://news.example.com/p1")
        batch_id = self.rg.submit_knowledge_link_batch([e1, e2])["batch_id"]

        ack = self.rg.acknowledge_receipts(batch_id, [e1])

        self.assertEqual("待回执", ack["batch_status"])
        self.assertFalse(ack["settled"])
        self.assertTrue(all(
            p["交付状态"] == RECEIPT_PENDING for p in self.kl.list_provenance()
        ))
        # 建边清单已回写证据去向
        self.assertIn(batch_id, self.rg._edge_row(e1)["证据去向"])

    def test_replay_after_interruption_is_idempotent(self) -> None:
        e1 = self._edge("edge-ab", "实体A", "实体B", "https://news.example.com/p1")
        e2 = self._edge("edge-ac", "实体A", "实体C", "https://news.example.com/p1")
        first = self.rg.submit_knowledge_link_batch([e1, e2])
        self.rg.acknowledge_receipts(first["batch_id"], [e1])
        links_before = len(store.rows("knowledge_link"))

        # 值班员中断后重投原批（含顺序调换）
        replay = self.rg.submit_knowledge_link_batch([e2, e1])

        self.assertTrue(replay["replayed"])
        self.assertEqual(first["batch_id"], replay["batch_id"])
        self.assertEqual(links_before, len(store.rows("knowledge_link")))
        self.assertEqual(first["batch"]["link_ids"], replay["batch"]["link_ids"])
        self.assertEqual(1, replay["batch"]["acknowledged_count"])

    def test_concurrent_submission_writes_single_link_per_entity(self) -> None:
        e1 = self._edge("edge-ab", "实体A", "实体B", "https://news.example.com/p1")
        e2 = self._edge("edge-ac", "实体A", "实体C", "https://news.example.com/p1")
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = [
                f.result()
                for f in [pool.submit(self.rg.submit_knowledge_link_batch, [e1, e2])
                          for _ in range(8)]
            ]
        self.assertEqual(1, len({o["batch_id"] for o in outcomes}))
        self.assertEqual(
            1, len([r for r in self._active_links() if r["源实体"] == "实体A"])
        )

    def test_full_receipts_settle_and_deliver(self) -> None:
        e1 = self._edge("edge-ab", "实体A", "实体B", "https://news.example.com/p1")
        e2 = self._edge("edge-ac", "实体A", "实体C", "https://news.example.com/p1")
        batch_id = self.rg.submit_knowledge_link_batch([e1, e2])["batch_id"]

        ack = self.rg.acknowledge_receipts(batch_id)  # 不传边 ID = 整批回执

        self.assertEqual("已核销", ack["batch_status"])
        self.assertTrue(ack["settled"])
        link_ids = {p["链接ID"] for p in self.kl.list_provenance()}
        self.assertTrue(all(
            p["交付状态"] == RECEIPT_DELIVERED
            for p in self.kl.list_provenance()
            if p["链接ID"] in link_ids
        ))
        self.assertIn("已核销", self.rg._edge_row(e1)["证据去向"])

    def test_invalidated_link_identity_is_reused_on_republish(self) -> None:
        e1 = self._edge("edge-ab", "实体A", "实体B", "https://news.example.com/p1")
        first = self.rg.submit_knowledge_link_batch([e1])
        a_row = next(r for r in self._active_links() if r["源实体"] == "实体A")
        original_id = a_row["链接ID"]
        a_row["status"] = "已失效"

        e2 = self._edge("edge-ad", "实体A", "实体D", "https://news.example.com/p2")
        self.rg.submit_knowledge_link_batch([e2])

        active_a = [r for r in self._active_links() if r["源实体"] == "实体A"]
        self.assertEqual(1, len(active_a))
        self.assertEqual(original_id, active_a[0]["链接ID"])  # 只认第一次身份
        self.assertEqual(
            1,
            len([r for r in store.rows("knowledge_link") if r["链接ID"] == original_id]),
        )

    def test_unverified_edges_never_create_batch(self) -> None:
        entry, _ = self.rg.create_entry({
            "边ID": "edge-xy", "源实体": "实体X", "目标实体": "实体Y",
            "关系类型": "引用关系", "抽取页面": "https://news.example.com/p3",
            "原文证据": "共现", "关联强度": "0.6",
        })
        entry["status"] = "已抽取"  # 未验证

        result = self.rg.submit_knowledge_link_batch(["edge-xy"])

        self.assertIsNone(result["batch"])
        self.assertEqual(["edge-xy"], result["rejected_edge_ids"])
        self.assertEqual([], self.rg.list_knowledge_link_batches())


if __name__ == "__main__":
    unittest.main()
