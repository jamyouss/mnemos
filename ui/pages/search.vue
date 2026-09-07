<script setup lang="ts">
import { byDay, byIntent, latencyStats, scoreBuckets, topFiles } from '~/utils/queryLog'

const api = useApi()
const intent = ref<string>('')
const { data, error, refresh } = await useAsyncData(
  'query-log',
  () => api.queryLog({ limit: 1000, intent: intent.value || undefined }),
  { watch: [intent] },
)

const entries = computed(() => data.value?.entries ?? [])
const stats = computed(() => latencyStats(entries.value))
const days = computed(() =>
  Object.entries(byDay(entries.value)).sort(([a], [b]) => a.localeCompare(b)).map(([label, count]) => ({ label, count })),
)
const intents = computed(() =>
  Object.entries(byIntent(entries.value)).map(([label, count]) => ({ label, count })).sort((a, b) => b.count - a.count),
)
const files = computed(() => topFiles(entries.value, 10).map((r) => ({ label: r.file, count: r.count })))
const scores = computed(() => scoreBuckets(entries.value).map((r) => ({ label: r.bucket, count: r.count })))
const recent = computed(() => [...entries.value].reverse().slice(0, 25))
</script>

<template>
  <div class="space-y-8">
    <div class="flex items-baseline justify-between gap-4">
      <h1 class="text-2xl font-semibold tracking-tight">Search</h1>
      <div class="flex items-center gap-2">
        <select v-model="intent" class="rounded-md border bg-background px-2 py-1 text-sm">
          <option value="">every intent</option>
          <option value="search">search</option>
          <option value="search_code">search_code</option>
          <option value="search_skills">search_skills</option>
          <option value="search_memory">search_memory</option>
        </select>
        <button class="text-sm text-muted-foreground hover:text-foreground" @click="refresh()">Refresh</button>
      </div>
    </div>

    <EmptyState v-if="error" title="Could not read the query log" detail="GET /api/query-log failed." />

    <EmptyState
      v-else-if="data && !data.enabled"
      title="Nothing is being recorded"
      detail="MNEMOS_QUERY_LOG_ENABLED is false, so this screen has no data to show — which is not the same as having had no searches. Set it in .env and restart rag-server."
    />

    <EmptyState
      v-else-if="!entries.length"
      title="No searches recorded yet"
      detail="The log is on and empty. Run a search from the CLI or an MCP client and this fills in."
    />

    <template v-else>
      <div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Searches" :value="stats.count" />
        <StatCard label="p50" :value="`${Math.round(stats.p50)} ms`" />
        <StatCard label="p95" :value="`${Math.round(stats.p95)} ms`" :tone="stats.p95 > 2000 ? 'warning' : 'default'" />
        <StatCard label="Slowest" :value="`${Math.round(stats.max)} ms`" />
      </div>

      <div class="grid gap-6 lg:grid-cols-2">
        <section class="space-y-3 rounded-lg border bg-card p-4">
          <h2 class="text-sm font-medium">Volume by day</h2>
          <BarList :rows="days" />
        </section>
        <section class="space-y-3 rounded-lg border bg-card p-4">
          <h2 class="text-sm font-medium">By intent</h2>
          <BarList :rows="intents" />
        </section>
        <section class="space-y-3 rounded-lg border bg-card p-4">
          <h2 class="text-sm font-medium">Top-1 score distribution</h2>
          <p class="text-xs text-muted-foreground">
            Hybrid fusion puts a normal top-1 between 0.5 and 0.8. Read the shape, not a threshold.
          </p>
          <BarList :rows="scores" />
        </section>
        <section class="space-y-3 rounded-lg border bg-card p-4">
          <h2 class="text-sm font-medium">Most returned files</h2>
          <p class="text-xs text-muted-foreground">
            A file topping this list across unrelated queries is usually noise, not a match.
          </p>
          <BarList :rows="files" empty="No file paths recorded." />
        </section>
      </div>

      <section class="space-y-3">
        <h2 class="text-sm font-medium uppercase tracking-wide text-muted-foreground">Recent searches</h2>
        <div class="overflow-hidden rounded-lg border">
          <table class="w-full text-sm">
            <thead class="bg-muted/50 text-left text-xs uppercase text-muted-foreground">
              <tr>
                <th class="px-4 py-2">Query</th><th class="px-4 py-2">Intent</th>
                <th class="px-4 py-2">Hits</th><th class="px-4 py-2">Latency</th><th class="px-4 py-2">Stages</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(e, i) in recent" :key="i" class="border-t">
                <td class="max-w-md truncate px-4 py-2" :title="e.query">{{ e.query }}</td>
                <td class="px-4 py-2 font-mono text-xs text-muted-foreground">{{ e.intent }}</td>
                <td class="px-4 py-2 tabular-nums">{{ e.n_results }}</td>
                <td class="px-4 py-2 tabular-nums">{{ Math.round(e.latency_ms) }} ms</td>
                <td class="px-4 py-2 text-xs text-muted-foreground">
                  <span v-if="e.reranker">reranker </span>
                  <span v-if="e.grader">grader </span>
                  <span v-if="e.cache_hit">cache-hit</span>
                  <span v-if="!e.reranker && !e.grader && !e.cache_hit">—</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </template>
  </div>
</template>
