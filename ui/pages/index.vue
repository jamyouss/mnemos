<script setup lang="ts">
import {
  ActivityIcon,
  BrainIcon,
  DatabaseIcon,
  GaugeIcon,
  RefreshCwIcon,
  SearchXIcon,
  SparklesIcon,
  ZapIcon,
} from '@lucide/vue'
import { byIntent, latencyStats, PERIODS, rates, scoreBuckets, since, timeSeries, topFiles, type Period } from '~/utils/queryLog'

definePageMeta({ title: 'Dashboard' })
useHead({ title: 'Dashboard' })

const api = useApi()
const period = ref<Period>('7d')

// Independent and lazy: one failing source greys out its own card, not the page.
const status = useAsyncData('status', () => api.status(), { lazy: true })
// 5000 is the server's cap per call; enough for 30 days of a single developer.
const log = useAsyncData('log-dashboard', () => api.queryLog({ limit: 5000 }), { lazy: true })
const memory = useAsyncData('memory-stats', () => api.memoryStats(), { lazy: true })
const health = useAsyncData('health-deps', () => api.healthDeps(), { lazy: true })
const config = useAsyncData('config', () => api.config(), { lazy: true })
const projects = useAsyncData('facets-code-tags', () => api.facets('mnemos_code', 'tags', 10), { lazy: true })

function refreshAll() {
  for (const source of [status, log, memory, health, config, projects]) source.refresh()
}

// ---- Query log, narrowed to the selected period -------------------------
const now = ref(Math.floor(Date.now() / 1000))
watch([period, () => log.data.value], () => (now.value = Math.floor(Date.now() / 1000)))
const fromTs = computed(() => now.value - PERIODS[period.value].seconds)
const entries = computed(() => since(log.data.value?.entries ?? [], fromTs.value))
const oldestLoaded = computed(() => log.data.value?.entries?.[0]?.ts)
// The tail is capped server-side: if it starts after the window opens, the
// window is only partly covered and the totals below undercount.
const truncated = computed(
  () => (log.data.value?.entries.length ?? 0) >= 5000 && (oldestLoaded.value ?? 0) > fromTs.value,
)

const latency = computed(() => latencyStats(entries.value))
const shares = computed(() => rates(entries.value))
const buckets = computed(() => timeSeries(entries.value, fromTs.value, now.value, PERIODS[period.value].bucket))

const intents = computed(() =>
  Object.entries(byIntent(entries.value))
    .sort((a, b) => b[1] - a[1])
    .map(([key]) => key),
)
const trafficSeries = computed(() => intents.value.map((key) => ({ key, label: key })))
const trafficData = computed(() => buckets.value.map((b) => ({ t: b.t, ...Object.fromEntries(intents.value.map((k) => [k, b.byIntent[k] ?? 0])) })))
const latencyData = computed(() => buckets.value.map((b) => ({ t: b.t, p50: Math.round(b.p50), p95: Math.round(b.p95) })))
const scores = computed(() => scoreBuckets(entries.value).map((r) => ({ label: r.bucket, count: r.count })).filter((r) => r.count))
const files = computed(() => topFiles(entries.value, 8).map((r) => ({ label: r.file, count: r.count })))

// ---- Index & memory ------------------------------------------------------
const collections = computed(() =>
  Object.entries(status.data.value?.collections ?? {})
    // Internal collections (cache, eval copies) are not content.
    .filter(([name]) => !name.endsWith('_cache') && !name.includes('_eval'))
    .map(([name, info]) => ({ label: name.replace(/^mnemos_/, ''), count: info.points_count ?? 0 })),
)
const totalChunks = computed(() => collections.value.reduce((n, c) => n + c.count, 0))
const memoryTypes = computed(() => Object.entries(memory.data.value?.by_type ?? {}).map(([label, count]) => ({ label, count })))
const projectRows = computed(() => (projects.data.value?.counts ?? []).map((c) => ({ label: c.value, count: c.count })))

// ---- Retrieval stages, as actually configured ---------------------------
/** Costs measured on this deployment. The question is never "is it on" but "is it worth it". */
const STAGES = [
  { key: null, name: 'Hybrid BM25 + RRF', note: 'the baseline, ~40 ms' },
  { key: 'mnemos_cache_enabled', name: 'Semantic cache', note: 'free, wins on repeats' },
  { key: 'mnemos_reranker_enabled', name: 'Cross-encoder reranker', note: '8 s per query on CPU — GPU-only' },
  { key: 'mnemos_mmr_enabled', name: 'MMR', note: 'only meaningful with the reranker' },
  { key: 'mnemos_grader_enabled', name: 'CRAG grader', note: '384× latency to prune 3 chunks in 89' },
  { key: 'mnemos_rewriter_enabled', name: 'Query rewriter', note: 'only runs when the grader rejects everything' },
  { key: 'mnemos_contextual_enabled', name: 'Contextual chunking', note: '3.6 s per chunk at index time' },
  { key: 'mnemos_query_log_enabled', name: 'Query log', note: 'feeds this dashboard' },
] as const

const configValues = computed(() => {
  const out: Record<string, unknown> = {}
  for (const section of config.data.value?.sections ?? []) for (const f of section.fields) out[f.key] = f.value
  return out
})
const stages = computed(() =>
  STAGES.map((s) => ({ ...s, on: s.key === null ? true : configValues.value[s.key] === true })),
)

const fmtPct = (x: number) => `${(x * 100).toFixed(x > 0 && x < 0.1 ? 1 : 0)}%`
const fmtMs = (x: number) => (x >= 1000 ? `${(x / 1000).toFixed(1)} s` : `${Math.round(x)} ms`)
</script>

<template>
  <div class="space-y-6">
    <PageHeader title="Dashboard" description="What the index holds, how search behaves, and what is switched on.">
      <template #actions>
        <ToggleGroup v-model="period" type="single" variant="outline" size="sm">
          <ToggleGroupItem v-for="(p, id) in PERIODS" :key="id" :value="id" :aria-label="p.label">{{ id }}</ToggleGroupItem>
        </ToggleGroup>
        <Button variant="outline" size="sm" @click="refreshAll">
          <RefreshCwIcon />
          Refresh
        </Button>
      </template>
    </PageHeader>

    <Alert v-if="status.error.value" variant="destructive">
      <AlertTitle>The mnemos server is not answering</AlertTitle>
      <AlertDescription>GET /api/status failed. Check the stack is up: <code>docker compose ps</code>.</AlertDescription>
    </Alert>

    <HealthStrip :health="health.data.value" :pending="health.pending.value" :log-enabled="log.data.value?.enabled" />

    <div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
      <StatCard label="Indexed chunks" :icon="DatabaseIcon" :value="status.data.value ? totalChunks.toLocaleString() : '…'" :hint="`${collections.length} collections`" />
      <StatCard label="Searches" :icon="ActivityIcon" :value="entries.length.toLocaleString()" :hint="PERIODS[period].label" />
      <StatCard
        label="p95 latency"
        :icon="GaugeIcon"
        :value="latency.count ? fmtMs(latency.p95) : '—'"
        :tone="latency.p95 > 2000 ? 'warning' : 'default'"
        :hint="latency.count ? `p50 ${fmtMs(latency.p50)}` : 'no searches'"
      />
      <StatCard label="Cache hits" :icon="ZapIcon" :value="entries.length ? fmtPct(shares.cacheHit) : '—'" hint="answered from the semantic cache" />
      <StatCard
        label="Empty results"
        :icon="SearchXIcon"
        :value="entries.length ? fmtPct(shares.zeroResults) : '—'"
        :tone="shares.zeroResults > 0.1 ? 'warning' : 'default'"
        hint="searches that returned nothing"
      />
      <StatCard
        label="Memories"
        :icon="BrainIcon"
        :value="memory.data.value ? (memory.data.value.by_status.approved ?? 0).toLocaleString() : '…'"
        :hint="memory.data.value ? `${memory.data.value.total} total · ${memory.data.value.by_status.rejected ?? 0} rejected` : undefined"
      />
    </div>

    <Alert v-if="truncated">
      <AlertTitle>Partial window</AlertTitle>
      <AlertDescription>
        Only the latest 5,000 searches are loaded and they start after this window opens: totals undercount.
      </AlertDescription>
    </Alert>

    <div class="grid gap-4 lg:grid-cols-3">
      <Card class="lg:col-span-2">
        <CardHeader>
          <CardTitle>Search traffic</CardTitle>
          <CardDescription>Searches per {{ PERIODS[period].bucket }}, by intent</CardDescription>
        </CardHeader>
        <CardContent>
          <EmptyState
            v-if="log.data.value && !log.data.value.enabled"
            title="Nothing is being recorded"
            detail="The query log is off, so there is no traffic to chart — which is not the same as having had no searches. Turn it on in Configuration."
          >
            <Button as-child size="sm" variant="outline"><NuxtLink to="/config">Open configuration</NuxtLink></Button>
          </EmptyState>
          <TimeChart
            v-else-if="trafficSeries.length"
            :key="`${period}-${intents.join()}`"
            :data="trafficData"
            :series="trafficSeries"
            :bucket="PERIODS[period].bucket"
          />
          <EmptyState v-else title="No searches in this window" detail="Run a search from the CLI or an MCP client." />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Index</CardTitle>
          <CardDescription>Chunks per collection</CardDescription>
        </CardHeader>
        <CardContent>
          <DonutChart :rows="collections" center-label="chunks" />
        </CardContent>
      </Card>
    </div>

    <div class="grid gap-4 lg:grid-cols-3">
      <Card class="lg:col-span-2">
        <CardHeader>
          <CardTitle>Latency</CardTitle>
          <CardDescription>p50 and p95 per {{ PERIODS[period].bucket }}</CardDescription>
        </CardHeader>
        <CardContent>
          <TimeChart
            v-if="latency.count"
            :key="period"
            kind="line"
            unit=" ms"
            :data="latencyData"
            :series="[{ key: 'p50', label: 'p50' }, { key: 'p95', label: 'p95' }]"
            :bucket="PERIODS[period].bucket"
          />
          <p v-else class="py-6 text-sm text-muted-foreground">No searches in this window.</p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Top-1 score</CardTitle>
          <CardDescription>Hybrid fusion puts a normal top-1 between 0.5 and 0.8. Read the shape, not a threshold.</CardDescription>
        </CardHeader>
        <CardContent>
          <BarList :rows="scores" empty="No scored searches in this window." />
        </CardContent>
      </Card>
    </div>

    <div class="grid gap-4 lg:grid-cols-3">
      <Card>
        <CardHeader>
          <CardTitle>Projects</CardTitle>
          <CardDescription>Code chunks per tag</CardDescription>
        </CardHeader>
        <CardContent>
          <p v-if="projects.error.value" class="text-sm text-muted-foreground">Tag counts unavailable.</p>
          <BarList v-else :rows="projectRows" mono empty="No tagged code yet." />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Most returned files</CardTitle>
          <CardDescription>One topping this list across unrelated queries is usually noise.</CardDescription>
        </CardHeader>
        <CardContent>
          <BarList :rows="files" mono empty="No file paths recorded." />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Memories</CardTitle>
          <CardDescription>By type</CardDescription>
        </CardHeader>
        <CardContent>
          <DonutChart :rows="memoryTypes" center-label="memories" empty="No memories yet." />
        </CardContent>
      </Card>
    </div>

    <Card>
      <CardHeader class="flex flex-row items-start justify-between gap-4">
        <div class="space-y-1.5">
          <CardTitle class="flex items-center gap-2"><SparklesIcon class="size-4" /> Retrieval stages</CardTitle>
          <CardDescription>Read from the running server. Each cost was measured on this index, not assumed.</CardDescription>
        </div>
        <Button as-child variant="outline" size="sm"><NuxtLink to="/config">Configure</NuxtLink></Button>
      </CardHeader>
      <CardContent>
        <p v-if="config.error.value" class="text-sm text-muted-foreground">Configuration unavailable: {{ config.error.value.message }}</p>
        <ul v-else class="grid gap-x-8 gap-y-3 md:grid-cols-2">
          <li v-for="s in stages" :key="s.name" class="flex items-center gap-3 text-sm">
            <Badge :variant="s.on ? 'default' : 'secondary'" class="w-12 justify-center">{{ config.pending.value && s.key ? '…' : s.on ? 'on' : 'off' }}</Badge>
            <span class="font-medium">{{ s.name }}</span>
            <span class="truncate text-muted-foreground" :title="s.note">{{ s.note }}</span>
          </li>
        </ul>
      </CardContent>
    </Card>
  </div>
</template>
