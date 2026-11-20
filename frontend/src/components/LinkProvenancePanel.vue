<template>
  <section class="provenance-panel" data-testid="link-provenance-panel">
    <header class="provenance-head">
      <h3>已交付链接出处台账</h3>
      <p class="provenance-hint">
        同一实体与外部 URI 仅保留一条有效链接；每条链接可展开其全部原始关系边出处，
        覆盖整批的回执到齐后才转为已交付。
      </p>
      <button class="btn ghost" data-testid="provenance-refresh" type="button" @click="reload">
        刷新台账
      </button>
    </header>

    <table v-if="items.length" class="data-table provenance-table" data-testid="provenance-table">
      <thead>
        <tr>
          <th>链接ID</th>
          <th>源实体</th>
          <th>外部URI</th>
          <th>出处边数</th>
          <th>交付状态</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in items" :key="item.链接ID" :data-testid="`provenance-row-${item.链接ID}`">
          <td>{{ item.链接ID }}</td>
          <td>{{ item.源实体 }}</td>
          <td>{{ item.外部URI }}</td>
          <td>{{ item.source_count }}</td>
          <td :data-testid="`provenance-status-${item.链接ID}`">{{ item.交付状态 }}</td>
          <td class="row-actions">
            <button
              class="link"
              type="button"
              :data-testid="`provenance-toggle-${item.链接ID}`"
              @click="toggle(item.链接ID)"
            >
              {{ expanded === item.链接ID ? '收起出处' : '展开出处' }}
            </button>
          </td>
        </tr>
      </tbody>
    </table>
    <p v-else class="empty-state">暂无流水线链接出处，关系图谱提交批次后在此核对。</p>

    <div v-if="expandedItem" class="provenance-detail" data-testid="provenance-detail">
      <table class="data-table">
        <thead>
          <tr>
            <th>批次ID</th>
            <th>边ID</th>
            <th>源实体</th>
            <th>目标实体</th>
            <th>来源页</th>
            <th>出处状态</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(source, index) in expandedItem.sources" :key="`${source.edge_id}-${index}`">
            <td>{{ source.batch_id }}</td>
            <td>{{ source.edge_id }}</td>
            <td>{{ source.source_entity }}</td>
            <td>{{ source.target_entity }}</td>
            <td>{{ source.evidence_page }}</td>
            <td>{{ source.delivered ? '已交付' : '待回执' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { listLinkProvenance, type ProvenanceItem } from '@/api/backend-service'

const items = ref<ProvenanceItem[]>([])
const expanded = ref<string | null>(null)
const expandedItem = ref<ProvenanceItem | null>(null)
const feedback = ref('')

async function reload() {
  feedback.value = ''
  try {
    const payload = await listLinkProvenance()
    items.value = payload.items
    if (expanded.value) {
      expandedItem.value = items.value.find((item) => item.链接ID === expanded.value) ?? null
    }
  } catch (error) {
    feedback.value = error instanceof Error ? error.message : '出处台账读取失败'
  }
}

function toggle(linkId: string) {
  if (expanded.value === linkId) {
    expanded.value = null
    expandedItem.value = null
    return
  }
  expanded.value = linkId
  expandedItem.value = items.value.find((item) => item.链接ID === linkId) ?? null
}

onMounted(reload)
</script>

<style scoped>
.provenance-panel {
  margin-top: 24px;
  padding: 16px;
  border: 1px solid var(--border-color, #d9dde3);
  border-radius: 10px;
  background: var(--panel-bg, #fff);
}

.provenance-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.provenance-head h3 {
  margin: 0;
}

.provenance-hint {
  flex-basis: 100%;
  margin: 0 0 8px;
  color: var(--muted, #6b7280);
  font-size: 13px;
}

.provenance-table {
  margin-top: 12px;
}

.provenance-detail {
  margin-top: 12px;
  padding: 12px;
  border: 1px dashed var(--border-color, #d9dde3);
  border-radius: 8px;
}
</style>
