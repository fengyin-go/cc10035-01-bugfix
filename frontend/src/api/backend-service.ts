import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type { ActionResult, EntryRow, OverviewResult, PageResult } from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

// ---------------------------------------------------------------------------
// 关系图谱 -> 知识链接 批次流水线
// ---------------------------------------------------------------------------

const PIPELINE_BASE = '/api/pipeline/relation-graph'

export type EdgeReceipt = {
  batch_id: string
  edge_id: string
  source_entity: string
  target_entity: string
  evidence_page: string
  links: { 链接ID: string; 源实体: string; 外部URI: string; 链接类型: string }[]
  receipt_status: string
  acknowledged?: boolean
}

export type LinkBatch = {
  batch_id: string
  edge_ids: string[]
  rejected_edge_ids: string[]
  状态: string
  receipt_count: number
  acknowledged_count: number
  link_ids: string[]
  created_link_ids: string[]
  reused_link_ids: string[]
}

export type BatchSubmitResult = {
  ok: boolean
  batch_id: string | null
  replayed: boolean
  batch: LinkBatch | null
  receipts: EdgeReceipt[]
  rejected_edge_ids?: string[]
  message: string
}

export async function submitKnowledgeLinkBatch(relationIds: string[]): Promise<BatchSubmitResult> {
  const response = await request(`${PIPELINE_BASE}/knowledge-link-batches`, {
    method: 'POST',
    body: JSON.stringify({ relation_ids: relationIds }),
  })
  if (!response.ok) throw new Error('知识链接批次提交失败')
  return await response.json()
}

export async function listKnowledgeLinkBatches(): Promise<{ ok: boolean; total: number; batches: LinkBatch[] }> {
  const response = await request(`${PIPELINE_BASE}/knowledge-link-batches`)
  if (!response.ok) throw new Error('批次台账读取失败')
  return await response.json()
}

export async function getKnowledgeLinkBatch(
  batchId: string,
): Promise<{ ok: boolean; batch: LinkBatch; receipts: EdgeReceipt[] }> {
  const response = await request(`${PIPELINE_BASE}/knowledge-link-batches/${batchId}`)
  if (!response.ok) throw new Error('批次出处读取失败')
  return await response.json()
}

export async function acknowledgeBatchReceipts(
  batchId: string,
  edgeIds?: string[],
): Promise<Record<string, unknown>> {
  const response = await request(`${PIPELINE_BASE}/knowledge-link-batches/${batchId}/receipts`, {
    method: 'POST',
    body: JSON.stringify({ edge_ids: edgeIds ?? null }),
  })
  if (!response.ok) throw new Error('出处回执登记失败')
  return await response.json()
}

export type ProvenanceItem = {
  链接ID: string
  源实体: string
  外部URI: string
  链接类型: string
  delivered: boolean
  交付状态: string
  source_count: number
  sources: {
    batch_id: string
    edge_id: string
    source_entity: string
    target_entity: string
    evidence_page: string
    delivered: boolean
  }[]
}

export async function listLinkProvenance(): Promise<{ ok: boolean; total: number; items: ProvenanceItem[] }> {
  const response = await request(`${PIPELINE_BASE}/link-provenance`)
  if (!response.ok) throw new Error('出处台账读取失败')
  return await response.json()
}
