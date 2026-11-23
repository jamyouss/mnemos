<script setup lang="ts">
import { RefreshCwIcon, TriangleAlertIcon } from '@lucide/vue'
import { byIntent, INTENT_LABELS, intentLabel, latencyStats, rates, scoreBuckets, topFiles } from '~/utils/queryLog'
import { isAdmin } from '~/utils/tenants'

definePageMeta({ title: 'Search log' })
useHead({ title: 'Search log' })

const api = useApi()
const { data: me } = await useWhoami()
const admin = computed(() => isAdmin(me.value))
const ALL = 'all'
const intent = ref<string>(ALL)
const filter = ref('')
const { data, error, refresh, pending } = await useAsyncData(
  'query-log',
  () => api.queryLog({ limit: 1000, intent: intent.value === ALL ? undefined : intent.value }),
  { watch: [intent], immediate: admin.value },
)

const playground = usePlaygroundTraffic()
const entries = computed(() => playground.keep(data.value?.entries ?? []))
const stats = computed(() => latencyStats(entries.value))
const shares = computed(() => rates(entries.value))
const intents = computed(() => Object.entries(byIntent(entries.value)).map(([intent, count]) => ({ label: intentLabel(intent), count })))
const files = computed(() => topFiles(entries.value, 10).map((r) => ({ label: r.file, count: r.count })))
const scores = computed(() => scoreBuckets(entries.value).map((r) => ({ label: r.bucket, count: r.count })))

// Paged over the loaded window, newest first: the same 1,000 searches the
// figures above describe, so the table and the stats never disagree.
const page = ref(1)
const pageSize = ref(25)
const matching = computed(() => {
  const q = filter.value.trim().toLowerCase()
  return [...entries.value].reverse().filter((e) => !q || e.query.toLowerCase().includes(q))
})
const recent = computed(() => matching.value.slice((page.value - 1) * pageSize.value, page.value * pageSize.value))
watch([filter, intent, pageSize, playground.include], () => (page.value = 1))

const fmtMs = (x: number) => (x >= 1000 ? `${(x / 1000).toFixed(1)} s` : `${Math.round(x)} ms`)
const fmtPct = (x: number) => `${(x * 100).toFixed(x > 0 && x < 0.1 ? 1 : 0)}%`
const fmtTime = (ts: number) => new Date(ts * 1000).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
const shortFile = (f?: string) => f?.replace('/data/codebase/', '') ?? '—'
</script>

<template>
  <div class="space-y-6">
    <PageHeader title="Search log" description="The last 1,000 searches, newest first.">
      <template v-if="admin" #actions>
        <label class="flex items-center gap-2 text-sm text-muted-foreground">
          <Switch v-model="playground.include.value" /> Include Playground searches
        </label>
        <Select v-model="intent">
          <SelectTrigger size="sm" class="w-44" aria-label="Search type"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem :value="ALL">All searches</SelectItem>
            <SelectItem v-for="(label, value) in INTENT_LABELS" :key="value" :value="value">{{ label }}</SelectItem>
          </SelectContent>
        </Select>
        <Button variant="outline" size="sm" :disabled="pending" @click="refresh()">
          <RefreshCwIcon :class="pending && 'animate-spin'" />
          Refresh
        </Button>
      </template>
    </PageHeader>

    <AdminOnly v-if="me && !admin" :me="me" what="The query log" />

    <EmptyState v-else-if="error" tone="error" title="Could not read the query log" :detail="error.message" />

    <EmptyState
      v-else-if="data && !data.enabled"
      title="Nothing is being recorded"
      detail="The query log is off, so this screen has no data — which is not the same as having had no searches."
    >
      <Button as-child size="sm" variant="outline"><NuxtLink to="/config">Turn it on in Configuration</NuxtLink></Button>
    </EmptyState>

    <EmptyState
      v-else-if="!entries.length"
      title="No searches recorded yet"
      detail="The log is on and empty. Run a search from the CLI or an MCP client and this fills in."
    />

    <template v-else>
      <div class="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <StatCard label="Searches" :value="stats.count.toLocaleString()" />
        <StatCard label="Median latency" :value="fmtMs(stats.p50)" />
        <StatCard label="95th percentile" :value="fmtMs(stats.p95)" :tone="stats.p95 > 2000 ? 'warning' : 'default'" />
        <StatCard label="Slowest" :value="fmtMs(stats.max)" />
        <StatCard label="Empty results" :value="fmtPct(shares.zeroResults)" :tone="shares.zeroResults > 0.1 ? 'warning' : 'default'" />
      </div>

      <div class="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>By intent</CardTitle>
            <CardDescription>Share of searches per tool</CardDescription>
          </CardHeader>
          <CardContent><DonutChart :rows="intents" center-label="searches" /></CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Top-1 score</CardTitle>
            <CardDescription>Read the shape, not a threshold.</CardDescription>
          </CardHeader>
          <CardContent><BarList :rows="scores" /></CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Most returned files</CardTitle>
            <CardDescription>Topping this across unrelated queries means noise.</CardDescription>
          </CardHeader>
          <CardContent><BarList :rows="files" mono empty="No file paths recorded." /></CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader class="flex flex-row flex-wrap items-center justify-between gap-4">
          <CardTitle>Recent searches</CardTitle>
          <Input v-model="filter" placeholder="Filter queries…" class="h-8 max-w-64" aria-label="Filter queries" />
        </CardHeader>
        <CardContent class="px-0">
          <p class="sr-only" aria-live="polite">{{ filter.trim() ? `${matching.length} matching searches` : '' }}</p>
          <div v-if="matching.length" class="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead class="pl-6">When</TableHead>
                  <TableHead>Query</TableHead>
                  <TableHead>Intent</TableHead>
                  <TableHead class="text-right">Hits</TableHead>
                  <TableHead class="text-right">Top-1</TableHead>
                  <TableHead class="text-right">Latency</TableHead>
                  <TableHead class="pr-6">Stages</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                <TableRow v-for="(e, i) in recent" :key="i">
                  <TableCell class="pl-6 whitespace-nowrap text-muted-foreground">{{ fmtTime(e.ts) }}</TableCell>
                  <TableCell class="max-w-sm min-w-48">
                    <p class="truncate" :title="e.query">{{ e.query }}</p>
                    <p class="truncate font-mono text-xs text-muted-foreground" :title="shortFile(e.top_files?.[0])">{{ shortFile(e.top_files?.[0]) }}</p>
                  </TableCell>
                  <TableCell><Badge variant="outline">{{ intentLabel(e.intent) }}</Badge></TableCell>
                  <TableCell class="text-right tabular-nums" :class="e.n_results === 0 && 'text-warning'">
                    <span class="inline-flex items-center gap-1">
                      <TriangleAlertIcon v-if="e.n_results === 0" class="size-3.5" aria-hidden="true" />
                      {{ e.n_results }}<span v-if="e.n_results === 0" class="sr-only"> (no results)</span>
                    </span>
                  </TableCell>
                  <TableCell class="text-right tabular-nums">{{ e.top_scores?.[0]?.toFixed(2) ?? '—' }}</TableCell>
                  <TableCell class="text-right tabular-nums whitespace-nowrap" :class="e.latency_ms > 2000 && 'text-warning'">
                    <span class="inline-flex items-center gap-1">
                      <TriangleAlertIcon v-if="e.latency_ms > 2000" class="size-3.5" aria-hidden="true" />
                      {{ fmtMs(e.latency_ms) }}<span v-if="e.latency_ms > 2000" class="sr-only"> (slow)</span>
                    </span>
                  </TableCell>
                  <TableCell class="pr-6">
                    <div class="flex gap-1">
                      <Badge v-if="e.cache_hit" variant="secondary">Cache</Badge>
                      <Badge v-if="e.reranker" variant="secondary">Rerank</Badge>
                      <Badge v-if="e.grader" variant="secondary">Grader</Badge>
                    </div>
                  </TableCell>
                </TableRow>
              </TableBody>
            </Table>
          </div>
          <p v-else class="px-6 py-4 text-sm text-muted-foreground">No query matches “{{ filter }}”.</p>
          <ListPager v-model:page="page" v-model:page-size="pageSize" :total="matching.length" class="px-6 pt-4" />
        </CardContent>
      </Card>
    </template>
  </div>
</template>
