<template>
  <section class="pipeline-panel" data-testid="relation-pipeline-panel">
    <header class="pipeline-head">
      <h3>实体知识链接数据批次</h3>
      <p class="pipeline-hint">
        提交已验证关系边（边ID，多个用逗号分隔）。同批边共端点只生成一条外链，
        中断后重投同一批边按首次外链身份幂等回放。
      </p>
    </header>

    <form class="pipeline-submit" @submit.prevent="submitBatch">
      <input
        v-model="edgeIdInput"
        data-testid="pipeline-edge-input"
        placeholder="例如 edge-ab, edge-ac"
      />
      <button class="btn primary" data-testid="pipeline-submit" type="submit" :disabled="submitting">
        {{ submitting ? '提交中…' : '提交知识链接批次' }}
      </button>
      <button class="btn ghost" data-testid="pipeline-refresh" type="button" @click="reloadBatches">
        刷新台账
      </button>
    </form>
    <p v-if="feedback" class="pipeline-feedback" data-testid="pipeline-feedback">{{ feedback }}</p>

    <table v-if="batches.length" class="data-table pipeline-table" data-testid="pipeline-batches">
      <thead>
        <tr>
          <th>批次ID</th>
          <th>关系边</th>
          <th>外链清单</th>
          <th>回执进度</th>
          <th>批次状态</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="batch in batches" :key="batch.batch_id" :data-testid="`batch-row-${batch.batch_id}`">
          <td>{{ batch.batch_id }}</td>
          <td>{{ batch.edge_ids.join('、') }}</td>
          <td>{{ batch.link_ids.join('、') || '—' }}</td>
          <td>{{ batch.acknowledged_count }} / {{ batch.receipt_count }}</td>
          <td :data-testid="`batch-status-${batch.batch_id}`">{{ batch.状态 }}</td>
          <td class="row-actions">
            <button
              class="link"
              type="button"
              :data-testid="`batch-detail-${batch.batch_id}`"
              @click="openDetail(batch.batch_id)"
            >
              核对出处
            </button>
            <button
              v-if="batch.状态 === '待回执'"
              class="link"
              type="button"
              :data-testid="`batch-ack-${batch.batch_id}`"
              @click="acknowledge(batch.batch_id)"
            >
              回传整批回执
            </button>
          </td>
        </tr>
      </tbody>
    </table>
    <p v-else class="empty-state">暂无知识链接批次，先在上方提交已验证关系边。</p>

    <div v-if="detail" class="pipeline-detail" data-testid="pipeline-detail">
      <header class="detail-head">
        <h4>批次 {{ detail.batch.batch_id }} 的边出处清单</h4>
        <button class="link" type="button" data-testid="pipeline-detail-close" @click="detail = null">
          关闭
        </button>
      </header>
      <table class="data-table pipeline-table">
        <thead>
          <tr>
            <th>边ID</th>
            <th>源实体</th>
            <th>目标实体</th>
            <th>来源页</th>
            <th>外链出处</th>
            <th>回执状态</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="receipt in detail.receipts" :key="receipt.edge_id" :data-testid="`receipt-row-${receipt.edge_id}`">
            <td>{{ receipt.edge_id }}</td>
            <td>{{ receipt.source_entity }}</td>
            <td>{{ receipt.target_entity }}</td>
            <td>{{ receipt.evidence_page }}</td>
            <td>
              <span v-for="link in receipt.links" :key="link.链接ID" class="link-chip">
                {{ link.链接ID }}
              </span>
              <span v-if="!receipt.links.length">—</span>
            </td>
            <td :data-testid="`receipt-status-${receipt.edge_id}`">{{ receipt.receipt_status }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import {
  acknowledgeBatchReceipts,
  getKnowledgeLinkBatch,
  listKnowledgeLinkBatches,
  submitKnowledgeLinkBatch,
  type EdgeReceipt,
  type LinkBatch,
} from '@/api/backend-service'

const edgeIdInput = ref('')
const submitting = ref(false)
const feedback = ref('')
const batches = ref<LinkBatch[]>([])
const detail = ref<{ batch: LinkBatch; receipts: EdgeReceipt[] } | null>(null)

async function reloadBatches() {
  try {
    const payload = await listKnowledgeLinkBatches()
    batches.value = payload.batches
  } catch (error) {
    feedback.value = error instanceof Error ? error.message : '批次台账读取失败'
  }
}

async function submitBatch() {
  submitting.value = true
  feedback.value = ''
  try {
    const ids = edgeIdInput.value
      .split(/[,，\s]+/)
      .map((item) => item.trim())
      .filter(Boolean)
    if (!ids.length) {
      feedback.value = '请至少填写一个关系边ID'
      return
    }
    const result = await submitKnowledgeLinkBatch(ids)
    if (!result.batch) {
      feedback.value = result.message
      return
    }
    feedback.value = result.replayed
      ? `批次重放：${result.batch_id}，复用首次外链身份，回执 ${result.receipts.length} 条`
      : `批次已生成：${result.batch_id}，外链 ${result.batch.link_ids.length} 条，回执 ${result.receipts.length} 条`
    edgeIdInput.value = ''
    await reloadBatches()
  } catch (error) {
    feedback.value = error instanceof Error ? error.message : '批次提交失败'
  } finally {
    submitting.value = false
  }
}

async function acknowledge(batchId: string) {
  feedback.value = ''
  try {
    const result = await acknowledgeBatchReceipts(batchId)
    feedback.value = String(result.message ?? '回执已登记')
    await reloadBatches()
    if (detail.value && detail.value.batch.batch_id === batchId) {
      await openDetail(batchId)
    }
  } catch (error) {
    feedback.value = error instanceof Error ? error.message : '回执登记失败'
  }
}

async function openDetail(batchId: string) {
  try {
    detail.value = await getKnowledgeLinkBatch(batchId)
  } catch (error) {
    feedback.value = error instanceof Error ? error.message : '批次出处读取失败'
  }
}

onMounted(reloadBatches)
</script>

<style scoped>
.pipeline-panel {
  margin-top: 24px;
  padding: 16px;
  border: 1px solid var(--border-color, #d9dde3);
  border-radius: 10px;
  background: var(--panel-bg, #fff);
}

.pipeline-head h3 {
  margin: 0 0 6px;
}

.pipeline-hint {
  margin: 0 0 12px;
  color: var(--muted, #6b7280);
  font-size: 13px;
}

.pipeline-submit {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.pipeline-submit input {
  flex: 1 1 260px;
  padding: 8px 10px;
  border: 1px solid var(--border-color, #d9dde3);
  border-radius: 6px;
}

.pipeline-feedback {
  margin: 10px 0 0;
  color: var(--primary, #2563eb);
  font-size: 13px;
}

.pipeline-table {
  margin-top: 14px;
}

.link-chip {
  display: inline-block;
  margin: 2px 4px 2px 0;
  padding: 1px 8px;
  border-radius: 999px;
  background: var(--chip-bg, #eef2ff);
  font-size: 12px;
}

.pipeline-detail {
  margin-top: 16px;
  padding: 12px;
  border: 1px dashed var(--border-color, #d9dde3);
  border-radius: 8px;
}

.detail-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
</style>
