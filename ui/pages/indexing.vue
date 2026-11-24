<script setup lang="ts">
import { DatabaseZapIcon, RefreshCwIcon } from '@lucide/vue'
import { refDebounced, useIntervalFn } from '@vueuse/core'
import type { IndexFileEvent, IndexJob } from '~/composables/useApi'
import { ago, runSummary } from '~/utils/connectors'
import { isAdmin } from '~/utils/tenants'

definePageMeta({ title: 'Indexing' })
useHead({ title: 'Indexing' })

const api = useApi()
const { data: me } = await useWhoami()
const admin = computed(() => isAdmin(me.value))

const { data: summary, refresh: refreshSummary } = await useAsyncData('indexing-summary', () => api.indexingSummary(24), { immediate: admin.value })
const { data: jobs, refresh: refreshJobs } = await useAsyncData('indexing-jobs', () => api.indexingJobs(20), { immediate: admin.value })
// Connector syncs are runs too; their detail lives on the Connectors page.
const { data: sync, refresh: refreshSync } = await useAsyncData('indexing-connectors', () => api.connectorSync().catch(() => null), { immediate: admin.value })

const ALL = 'all'
const collection = ref(ALL)
const channel = ref(ALL)
const op = ref(ALL)
const directory = ref('')
const directoryApplied = refDebounced(directory, 400)
const eventFilters = computed(() => ({
  collection: collection.value === ALL ? undefined : collection.value,
  channel: channel.value === ALL ? undefined : channel.value,
  op: op.value === ALL ? undefined : op.value,
  directory: directoryApplied.value.trim() || undefined,
}))
const { data: events, error, refresh: refreshEvents, pending } = await useAsyncData(
  'indexing-events',
  () => api.indexingEvents({ ...eventFilters.value, limit: 50 }),
  { watch: [eventFilters], immediate: admin.value },
)
const older = ref<IndexFileEvent[]>([])
const cursor = ref<number | null>(null)
watch(events, (page) => {
  older.value = []
  cursor.value = page?.next_before ?? null
}, { immediate: true })
const rows = computed(() => [...(events.value?.items ?? []), ...older.value])
const loadingMore = ref(false)
async function loadMore() {
  if (cursor.value === null) return
  loadingMore.value = true
  try {
    const page = await api.indexingEvents({ ...eventFilters.value, before: cursor.value, limit: 50 })
    older.value.push(...page.items)
    cursor.value = page.next_before
  } finally {
    loadingMore.value = false
  }
}

const now = ref(Date.now())
const running = computed(() => (jobs.value?.items ?? []).some((j) => j.state === 'running' && !j.stalled))
function refreshAll() {
  now.value = Date.now()
  refreshSummary()
  refreshJobs()
  refreshSync()
  // Keep a page the user scrolled into; reload only the first one.
  if (!older.value.length) refreshEvents()
}
// Every 3 s while a reindex runs, every 15 s otherwise.
useIntervalFn(refreshAll, () => (running.value ? 3000 : 15_000))

const OPS: { value: string; label: string }[] = [
  { value: 'indexed', label: 'Indexed' },
  { value: 'deleted', label: 'Deleted' },
  { value: 'skipped', label: 'Skipped' },
  { value: 'failed', label: 'Failed' },
]
const CHANNEL_LABELS: Record<string, string> = {
  watcher: 'Watcher', hook: 'Git hooks', connector: 'Connectors', cli: 'CLI', dashboard: 'Dashboard',
  mcp: 'MCP', api: 'API', script: 'Scripts',
}
const channelLabel = (c: string) => CHANNEL_LABELS[c] ?? c
const COLLECTIONS = ['mnemos_code', 'mnemos_knowledge', 'mnemos_docs', 'mnemos_skills']

const openJob = ref<string | null>(null)
const jobState = (j: IndexJob) => (j.stalled ? 'Stalled' : { running: 'Running', done: 'Done', failed: 'Failed' }[j.state])
const percent = (j: IndexJob) => (j.total ? Math.round((j.processed / j.total) * 100) : j.state === 'done' ? 100 : 0)
const fmtTime = (iso: string) => new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
const fmtMs = (ms: number | null) => (ms === null ? '—' : ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${ms} ms`)
const shortPath = (p: string) => p.replace('/data/codebase/', '')
const opVariant = (o: string) => (o === 'failed' ? 'destructive' : o === 'indexed' ? 'secondary' : 'outline')
const connectorRuns = computed(() => Object.entries(sync.value?.runs ?? {}))
</script>

<template>
  <div class="space-y-6">
    <PageHeader title="Indexing">
      <template #description>
        Bulk reindexes, and every file the watcher, git hooks, connectors, the CLI or MCP sent to the
        index.<template v-if="summary"> Kept {{ summary.retention_days }} days.</template>
      </template>
      <template v-if="admin" #actions>
        <Button variant="outline" size="sm" :disabled="pending" @click="refreshAll">
          <RefreshCwIcon :class="pending && 'animate-spin'" /> Refresh
        </Button>
      </template>
    </PageHeader>

    <AdminOnly v-if="me && !admin" :me="me" what="The indexing activity" />

    <EmptyState v-else-if="error" tone="error" title="Could not read the indexing activity" :detail="error.message" />

    <template v-else-if="summary">
      <div class="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="Files indexed · 24 h" :value="summary.counts.indexed.toLocaleString()" />
        <StatCard label="Deleted · 24 h" :value="summary.counts.deleted.toLocaleString()" />
        <StatCard label="Skipped · 24 h" :value="summary.counts.skipped.toLocaleString()" />
        <StatCard label="Failed · 24 h" :value="summary.counts.failed.toLocaleString()" :tone="summary.counts.failed ? 'warning' : 'default'" />
      </div>

      <div class="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Last activity per channel</CardTitle>
            <CardDescription>A channel that stopped sending is the first thing to check when files look stale.</CardDescription>
          </CardHeader>
          <CardContent>
            <ul v-if="summary.channels.length" class="divide-y">
              <li v-for="c in summary.channels" :key="c.channel" class="flex items-baseline justify-between gap-4 py-2">
                <span class="font-medium">{{ channelLabel(c.channel) }}</span>
                <span class="min-w-0 truncate text-right text-sm text-muted-foreground" :title="c.last_file ?? ''">
                  {{ ago(c.last_at, now) }}<template v-if="c.last_file"> · <span class="font-mono text-xs">{{ shortPath(c.last_file) }}</span></template>
                </span>
              </li>
            </ul>
            <p v-else class="text-sm text-muted-foreground">No file has reached the index since this history began.</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Connector syncs</CardTitle>
            <CardDescription>What each source last did. Details and errors on the <NuxtLink to="/connectors" class="underline">Connectors</NuxtLink> page.</CardDescription>
          </CardHeader>
          <CardContent>
            <ul v-if="connectorRuns.length" class="divide-y">
              <li v-for="[name, run] in connectorRuns" :key="name" class="flex items-baseline justify-between gap-4 py-2">
                <span class="min-w-0 truncate font-medium">{{ name }}</span>
                <span class="text-sm" :class="run.state === 'failed' ? 'text-destructive' : 'text-muted-foreground'">{{ runSummary(run, now) }}</span>
              </li>
            </ul>
            <p v-else class="text-sm text-muted-foreground">No connector has reported a sync.</p>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Reindex jobs</CardTitle>
          <CardDescription>Bulk reindexes from the CLI, the API or MCP. A job whose progress stops moving for two minutes is marked stalled: its server likely restarted.</CardDescription>
        </CardHeader>
        <CardContent class="px-0">
          <div v-if="jobs?.items.length" class="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead class="pl-6">Started</TableHead>
                  <TableHead>What</TableHead>
                  <TableHead>By</TableHead>
                  <TableHead class="w-48">Progress</TableHead>
                  <TableHead class="text-right">Chunks</TableHead>
                  <TableHead class="text-right">Failed</TableHead>
                  <TableHead class="pr-6">State</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                <template v-for="j in jobs.items" :key="j.id">
                  <TableRow class="cursor-pointer" tabindex="0" :aria-expanded="openJob === j.id" @click="openJob = openJob === j.id ? null : j.id" @keydown.enter="openJob = openJob === j.id ? null : j.id">
                    <TableCell class="pl-6 whitespace-nowrap text-muted-foreground">{{ fmtTime(j.started_at) }}</TableCell>
                    <TableCell class="max-w-sm">
                      <p>{{ j.collection }}</p>
                      <p class="truncate font-mono text-xs text-muted-foreground" :title="j.path">{{ shortPath(j.path) }}</p>
                    </TableCell>
                    <TableCell class="whitespace-nowrap">{{ j.actor }} <Badge variant="outline" class="ml-1 text-[11px]">{{ channelLabel(j.channel) }}</Badge></TableCell>
                    <TableCell>
                      <div class="h-1.5 w-full overflow-hidden rounded-full bg-muted" role="progressbar" :aria-valuenow="percent(j)" aria-valuemin="0" aria-valuemax="100">
                        <div class="h-full rounded-full bg-primary transition-all" :style="{ width: `${percent(j)}%` }" />
                      </div>
                      <p class="mt-1 text-xs text-muted-foreground tabular-nums">{{ j.processed.toLocaleString() }} / {{ j.total.toLocaleString() }} files</p>
                    </TableCell>
                    <TableCell class="text-right tabular-nums">{{ j.chunks.toLocaleString() }}</TableCell>
                    <TableCell class="text-right tabular-nums" :class="j.failed > 0 && 'text-destructive'">{{ j.failed }}</TableCell>
                    <TableCell class="pr-6">
                      <Badge :variant="j.state === 'failed' || j.stalled ? 'destructive' : j.state === 'running' ? 'secondary' : 'outline'">{{ jobState(j) }}</Badge>
                    </TableCell>
                  </TableRow>
                  <TableRow v-if="openJob === j.id">
                    <TableCell colspan="7" class="bg-muted/30 px-6 text-xs">
                      <p class="text-muted-foreground">
                        {{ j.full ? 'Whole tree' : 'One file' }} · {{ j.empty }} file(s) without chunks ·
                        {{ j.finished_at ? `finished ${fmtTime(j.finished_at)}` : 'not finished' }}
                      </p>
                      <p v-if="j.error" class="mt-2 font-mono text-destructive">{{ j.error }}</p>
                      <ul v-if="j.errors.length" class="mt-2 space-y-1 font-mono">
                        <li v-for="e in j.errors" :key="e.file_path"><span class="text-muted-foreground">{{ shortPath(e.file_path) }}</span> — <span class="text-destructive">{{ e.error }}</span></li>
                      </ul>
                    </TableCell>
                  </TableRow>
                </template>
              </TableBody>
            </Table>
          </div>
          <p v-else class="px-6 text-sm text-muted-foreground">No bulk reindex since this history began.</p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader class="flex flex-row flex-wrap items-center justify-between gap-3">
          <CardTitle>Files</CardTitle>
          <div class="flex flex-wrap items-center gap-2">
            <Input v-model="directory" type="search" placeholder="Under directory…" class="h-8 w-56 font-mono text-xs" aria-label="Only files under this directory" />
            <Select v-model="collection">
              <SelectTrigger size="sm" class="w-40" aria-label="Collection"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem :value="ALL">Every collection</SelectItem>
                <SelectItem v-for="c in COLLECTIONS" :key="c" :value="c">{{ c }}</SelectItem>
              </SelectContent>
            </Select>
            <Select v-model="channel">
              <SelectTrigger size="sm" class="w-36" aria-label="Channel"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem :value="ALL">Every channel</SelectItem>
                <SelectItem v-for="(label, value) in CHANNEL_LABELS" :key="value" :value="value">{{ label }}</SelectItem>
              </SelectContent>
            </Select>
            <Select v-model="op">
              <SelectTrigger size="sm" class="w-36" aria-label="Outcome"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem :value="ALL">Any outcome</SelectItem>
                <SelectItem v-for="o in OPS" :key="o.value" :value="o.value">{{ o.label }}</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </CardHeader>
        <CardContent class="px-0">
          <div v-if="rows.length" class="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead class="pl-6">When</TableHead>
                  <TableHead>Outcome</TableHead>
                  <TableHead>File</TableHead>
                  <TableHead>Channel</TableHead>
                  <TableHead class="text-right">Chunks</TableHead>
                  <TableHead class="pr-6 text-right">Took</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                <TableRow v-for="e in rows" :key="e.id" :class="e.op === 'failed' && 'bg-destructive/5'">
                  <TableCell class="pl-6 whitespace-nowrap text-muted-foreground">{{ fmtTime(e.at) }}</TableCell>
                  <TableCell><Badge :variant="opVariant(e.op)">{{ e.op }}</Badge></TableCell>
                  <TableCell class="max-w-lg min-w-64">
                    <p class="truncate font-mono text-xs" :title="e.file_path">{{ shortPath(e.file_path) }}</p>
                    <p v-if="e.error" class="truncate text-xs text-destructive" :title="e.error">{{ e.error }}</p>
                    <p v-else class="text-xs text-muted-foreground">{{ e.collection }} · {{ e.actor }}</p>
                  </TableCell>
                  <TableCell class="whitespace-nowrap">{{ channelLabel(e.channel) }}</TableCell>
                  <TableCell class="text-right tabular-nums">{{ e.chunks || '—' }}</TableCell>
                  <TableCell class="pr-6 text-right tabular-nums whitespace-nowrap">{{ fmtMs(e.duration_ms) }}</TableCell>
                </TableRow>
              </TableBody>
            </Table>
          </div>
          <EmptyState v-else :icon="DatabaseZapIcon" title="No file recorded" detail="Files pushed or deleted by any channel show up here, newest first." />
          <div v-if="cursor !== null" class="flex justify-center px-6 pt-4">
            <Button variant="outline" size="sm" :disabled="loadingMore" @click="loadMore">{{ loadingMore ? 'Loading…' : 'Load older files' }}</Button>
          </div>
        </CardContent>
      </Card>
    </template>
  </div>
</template>
