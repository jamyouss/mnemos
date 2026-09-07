<script setup lang="ts">
const api = useApi()
const { data, error, refresh } = await useAsyncData('memories', () => api.memories())

const search = ref('')
const type = ref('')
const status = ref('approved')
const busy = ref<string | null>(null)

const all = computed(() => data.value?.entries ?? [])
const types = computed(() => [...new Set(all.value.map((m) => m.memory_type).filter(Boolean))].sort())

const rows = computed(() =>
  all.value
    .filter((m) => !status.value || m.status === status.value)
    .filter((m) => !type.value || m.memory_type === type.value)
    .filter((m) => {
      const q = search.value.trim().toLowerCase()
      if (!q) return true
      return m.content.toLowerCase().includes(q) || (m.tags ?? []).some((t) => t.toLowerCase().includes(q))
    })
    .sort((a, b) => (b.created_at ?? '').localeCompare(a.created_at ?? '')),
)

async function reject(id: string) {
  busy.value = id
  try {
    await api.reviewMemory(id, 'reject')
    await refresh()
  } finally {
    busy.value = null
  }
}
</script>

<template>
  <div class="space-y-6">
    <div>
      <h1 class="text-2xl font-semibold tracking-tight">Memories</h1>
      <p class="mt-1 text-sm text-muted-foreground">
        Memories are written searchable. This screen is for taking a wrong one back out, not for letting
        good ones in — search returns <code class="text-xs">approved</code> entries only.
      </p>
    </div>

    <EmptyState v-if="error" title="Could not load memories" detail="GET /api/memory failed." />

    <template v-else>
      <div class="flex flex-wrap items-center gap-2">
        <input
          v-model="search"
          placeholder="Filter by content or tag…"
          class="min-w-64 flex-1 rounded-md border bg-background px-3 py-1.5 text-sm"
        >
        <select v-model="type" class="rounded-md border bg-background px-2 py-1.5 text-sm">
          <option value="">every type</option>
          <option v-for="t in types" :key="t" :value="t">{{ t }}</option>
        </select>
        <select v-model="status" class="rounded-md border bg-background px-2 py-1.5 text-sm">
          <option value="approved">approved</option>
          <option value="rejected">rejected</option>
          <option value="pending">pending</option>
          <option value="">every status</option>
        </select>
        <span class="text-sm tabular-nums text-muted-foreground">{{ rows.length }}</span>
      </div>

      <EmptyState
        v-if="!rows.length"
        title="Nothing matches"
        detail="Memories come from your commits, the MCP tool and `mnemos memory add`."
      />

      <ul v-else class="space-y-3">
        <li v-for="m in rows" :key="m.id" class="rounded-lg border bg-card p-4">
          <div class="flex items-start justify-between gap-4">
            <div class="min-w-0 flex-1">
              <div class="flex flex-wrap items-center gap-2 text-xs">
                <span class="rounded bg-muted px-1.5 py-0.5 font-medium">{{ m.memory_type }}</span>
                <span
                  v-for="t in m.tags"
                  :key="t"
                  class="rounded bg-muted/60 px-1.5 py-0.5 font-mono text-muted-foreground"
                >{{ t }}</span>
                <span v-if="m.status !== 'approved'" class="text-[hsl(var(--warning))]">{{ m.status }}</span>
              </div>
              <p class="mt-2 whitespace-pre-wrap text-sm">{{ m.content }}</p>
              <p class="mt-2 font-mono text-[11px] text-muted-foreground">
                {{ m.id }}<span v-if="m.created_at"> · {{ new Date(m.created_at).toLocaleDateString() }}</span>
              </p>
            </div>
            <button
              v-if="m.status !== 'rejected'"
              class="shrink-0 rounded-md border px-2.5 py-1 text-xs text-destructive hover:bg-destructive/10 disabled:opacity-50"
              :disabled="busy === m.id"
              @click="reject(m.id)"
            >
              {{ busy === m.id ? '…' : 'Reject' }}
            </button>
          </div>
        </li>
      </ul>
    </template>
  </div>
</template>
