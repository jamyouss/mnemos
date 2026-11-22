<script setup lang="ts">
import { RefreshCwIcon } from '@lucide/vue'
import { byIntent, latencyStats, rates, scoreBuckets, topFiles } from '~/utils/queryLog'

definePageMeta({ title: 'Search' })
useHead({ title: 'Search' })

const api = useApi()
const ALL = 'all'
const intent = ref<string>(ALL)
const filter = ref('')
const { data, error, refresh, pending } = await useAsyncData(
  'query-log',
  () => api.queryLog({ limit: 1000, intent: intent.value === ALL ? undefined : intent.value }),
  { watch: [intent] },
)

const entries = computed(() => data.value?.entries ?? [])
const stats = computed(() => latencyStats(entries.value))
const shares = computed(() => rates(entries.value))
const intents = computed(() => Object.entries(byIntent(entries.value)).map(([label, count]) => ({ label, count })))
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
watch([filter, intent, pageSize], () => (page.value = 1))

const fmtMs = (x: number) => (x >= 1000 ? `${(x / 1000).toFixed(1)} s` : `${Math.round(x)} ms`)
const fmtTime = (ts: number) => new Date(ts * 1000).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
const shortFile = (f?: string) => f?.replace('/data/codebase/', '') ?? '—'
</script>

<template>
  <div class="space-y-6">
    <PageHeader title="Search" description="The last 1,000 recorded searches, newest first.">
      <template #actions>
        <Select v-model="intent">
          <SelectTrigger size="sm" class="w-44"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem :value="ALL">Every intent</SelectItem>
            <SelectItem value="search">search</SelectItem>
            <SelectItem value="search_code">search_code</SelectItem>
            <SelectItem value="search_skills">search_skills</SelectItem>
            <SelectItem value="search_memory">search_memory</SelectItem>
          </SelectContent>
        </Select>
        <Button variant="outline" size="sm" :disabled="pending" @click="refresh()">
          <RefreshCwIcon :class="pending && 'animate-spin'" />
          Refresh
        </Button>
      </template>
    </PageHeader>

    <EmptyState v-if="error" title="Could not read the query log" detail="GET /api/query-log failed." />

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
      <div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <StatCard label="Searches" :value="stats.count.toLocaleString()" />
        <StatCard label="p50" :value="fmtMs(stats.p50)" />
        <StatCard label="p95" :value="fmtMs(stats.p95)" :tone="stats.p95 > 2000 ? 'warning' : 'default'" />
        <StatCard label="Slowest" :value="fmtMs(stats.max)" />
        <StatCard label="Empty results" :value="`${Math.round(shares.zeroResults * 100)}%`" :tone="shares.zeroResults > 0.1 ? 'warning' : 'default'" />
      </div>

      <div class="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader><CardTitle>By intent</CardTitle></CardHeader>
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
        <CardHeader class="flex flex-row items-center justify-between gap-4">
          <CardTitle>Recent searches</CardTitle>
          <Input v-model="filter" placeholder="Filter queries…" class="h-8 max-w-64" />
        </CardHeader>
        <CardContent class="px-0">
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
                <TableCell class="max-w-sm">
                  <p class="truncate" :title="e.query">{{ e.query }}</p>
                  <p class="truncate font-mono text-xs text-muted-foreground" :title="shortFile(e.top_files?.[0])">{{ shortFile(e.top_files?.[0]) }}</p>
                </TableCell>
                <TableCell><Badge variant="outline" class="font-mono">{{ e.intent }}</Badge></TableCell>
                <TableCell class="text-right tabular-nums" :class="e.n_results === 0 && 'text-warning'">{{ e.n_results }}</TableCell>
                <TableCell class="text-right tabular-nums">{{ e.top_scores?.[0]?.toFixed(2) ?? '—' }}</TableCell>
                <TableCell class="text-right tabular-nums" :class="e.latency_ms > 2000 && 'text-warning'">{{ fmtMs(e.latency_ms) }}</TableCell>
                <TableCell class="pr-6">
                  <div class="flex gap-1">
                    <Badge v-if="e.cache_hit" variant="secondary">cache</Badge>
                    <Badge v-if="e.reranker" variant="secondary">rerank</Badge>
                    <Badge v-if="e.grader" variant="secondary">grader</Badge>
                  </div>
                </TableCell>
              </TableRow>
            </TableBody>
          </Table>
          <p v-if="!matching.length" class="px-6 py-4 text-sm text-muted-foreground">No query matches “{{ filter }}”.</p>
          <ListPager v-model:page="page" v-model:page-size="pageSize" :total="matching.length" class="px-6 pt-4" />
        </CardContent>
      </Card>
    </template>
  </div>
</template>
