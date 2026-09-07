<script setup lang="ts">
const api = useApi()
const { data: status, error, refresh } = await useAsyncData('status', () => api.status())
const { data: log } = await useAsyncData('log-head', () => api.queryLog({ limit: 200 }))

const collections = computed(() =>
  Object.entries(status.value?.collections ?? {})
    .map(([name, info]) => ({ name, ...info }))
    .sort((a, b) => (b.points_count ?? 0) - (a.points_count ?? 0)),
)

const totalChunks = computed(() =>
  collections.value.reduce((sum, c) => sum + (c.points_count ?? 0), 0),
)

const lastSearch = computed(() => {
  const entries = log.value?.entries ?? []
  const last = entries[entries.length - 1]
  return last ? new Date(last.ts * 1000).toLocaleString() : '—'
})

/**
 * Stage costs measured on this deployment, not guesses. They are shown next
 * to the flags because the question is never "is it on" but "is it worth it".
 */
const stages = [
  { name: 'Hybrid BM25 + RRF', state: 'always on', note: 'the baseline, ~40 ms' },
  { name: 'Semantic cache', state: 'on', note: 'free, wins on repeats' },
  { name: 'Cross-encoder reranker', state: 'off', note: '8 s per query on CPU — GPU-only per the roadmap' },
  { name: 'MMR', state: 'off', note: 'only meaningful with the reranker' },
  { name: 'CRAG grader + rewriter', state: 'off', note: '384× latency to prune 3 chunks in 89' },
  { name: 'Contextual chunking', state: 'off', note: '3.6 s per chunk at index time (~28 h full reindex)' },
]
</script>

<template>
  <div class="space-y-8">
    <div class="flex items-baseline justify-between">
      <h1 class="text-2xl font-semibold tracking-tight">System</h1>
      <button class="text-sm text-muted-foreground hover:text-foreground" @click="refresh()">Refresh</button>
    </div>

    <EmptyState
      v-if="error"
      title="The mnemos server is not answering"
      :detail="`GET /api/status failed. Check the stack is up: docker compose ps.`"
    />

    <template v-else>
      <div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Indexed chunks" :value="totalChunks.toLocaleString()" />
        <StatCard label="Collections" :value="collections.length" />
        <StatCard
          label="Query log"
          :value="log?.enabled ? 'recording' : 'off'"
          :tone="log?.enabled ? 'success' : 'warning'"
          :hint="log?.enabled ? `${log?.count ?? 0} recent entries` : 'MNEMOS_QUERY_LOG_ENABLED=false'"
        />
        <StatCard label="Last search" :value="lastSearch" />
      </div>

      <section class="space-y-3">
        <h2 class="text-sm font-medium uppercase tracking-wide text-muted-foreground">Collections</h2>
        <div class="overflow-hidden rounded-lg border">
          <table class="w-full text-sm">
            <thead class="bg-muted/50 text-left text-xs uppercase text-muted-foreground">
              <tr><th class="px-4 py-2">Name</th><th class="px-4 py-2">Points</th><th class="px-4 py-2">Status</th></tr>
            </thead>
            <tbody>
              <tr v-for="c in collections" :key="c.name" class="border-t">
                <td class="px-4 py-2 font-mono text-xs">{{ c.name }}</td>
                <td class="px-4 py-2 tabular-nums">{{ (c.points_count ?? 0).toLocaleString() }}</td>
                <td class="px-4 py-2">
                  <span :class="c.status === 'green' ? 'text-[hsl(var(--success))]' : 'text-[hsl(var(--warning))]'">
                    {{ c.error ?? c.status ?? '—' }}
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="space-y-3">
        <h2 class="text-sm font-medium uppercase tracking-wide text-muted-foreground">Retrieval stages</h2>
        <p class="text-sm text-muted-foreground">
          Each was measured on this index rather than assumed. A stage that is off is off for a reason.
        </p>
        <div class="overflow-hidden rounded-lg border">
          <table class="w-full text-sm">
            <tbody>
              <tr v-for="s in stages" :key="s.name" class="border-t first:border-t-0">
                <td class="px-4 py-2">{{ s.name }}</td>
                <td class="px-4 py-2">
                  <span
                    class="rounded px-1.5 py-0.5 text-xs"
                    :class="s.state === 'off' ? 'bg-muted text-muted-foreground' : 'bg-[hsl(var(--success))]/15 text-[hsl(var(--success))]'"
                  >{{ s.state }}</span>
                </td>
                <td class="px-4 py-2 text-muted-foreground">{{ s.note }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </template>
  </div>
</template>
