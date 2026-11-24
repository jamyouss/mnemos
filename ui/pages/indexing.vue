<script setup lang="ts">
import { ChevronRightIcon, DatabaseZapIcon, RefreshCwIcon } from '@lucide/vue'
import { refDebounced, useIntervalFn } from '@vueuse/core'
import type { IndexFileEvent, IndexJob } from '~/composables/useApi'
import { CHANNELS, channelActivity, channelLabel } from '~/utils/channels'
import { pendingSummary, runSummary, serviceSummary } from '~/utils/connectors'
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
const announced = computed(() => {
  if (!events.value) return ''
  const n = rows.value.length
  return `${n} ${n === 1 ? 'file' : 'files'} loaded${cursor.value !== null ? ', older ones available' : ''}`
})
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
  { value: 'refused', label: 'Refused' },
]
const opLabel = (o: string) => OPS.find((x) => x.value === o)?.label ?? o
const COLLECTIONS = ['mnemos_code', 'mnemos_knowledge', 'mnemos_docs', 'mnemos_skills']

const openJob = ref<string | null>(null)
const toggleJob = (id: string) => { openJob.value = openJob.value === id ? null : id }
const jobState = (j: IndexJob) => (j.stalled ? 'Stalled' : { running: 'Running', done: 'Done', partial: 'Done with errors', failed: 'Failed' }[j.state])
const jobBadge = (j: IndexJob) => {
  if (j.state === 'failed' || j.stalled) return { variant: 'destructive' as const, class: '' }
  if (j.state === 'partial') return { variant: 'outline' as const, class: 'border-warning text-warning' }
  return { variant: j.state === 'running' ? 'secondary' as const : 'outline' as const, class: '' }
}
const percent = (j: IndexJob) => (j.total ? Math.round((j.processed / j.total) * 100) : j.state === 'done' || j.state === 'partial' ? 100 : 0)
const files = (n: number) => `${n.toLocaleString()} ${n === 1 ? 'file' : 'files'}`
const fmtTime = (iso: string) => new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
const fmtMs = (ms: number | null) => (ms === null ? '—' : ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${ms} ms`)
const shortPath = (p: string) => p.replace('/data/codebase/', '')
const opVariant = (o: string) => (o === 'failed' || o === 'refused' ? 'destructive' : o === 'indexed' ? 'secondary' : 'outline')
const connectorRuns = computed(() => Object.entries(sync.value?.runs ?? {}))
const channelRows = computed(() => (summary.value?.channels ?? []).map((c) => ({
  ...c,
  activity: channelActivity(c.last_at, now.value, summary.value!.retention_days),
})))
const service = computed(() => serviceSummary(sync.value?.service_seen_at, now.value))
const waiting = computed(() => pendingSummary(sync.value, now.value))
// A service that never reported is only worth a warning once connectors are in use.
const serviceTone = computed(() => {
  if (service.value.health === 'silent') return 'text-destructive'
  if (service.value.health === 'never' && (connectorRuns.value.length || waiting.value)) return 'text-warning'
  return 'text-muted-foreground'
})
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
      <div class="grid grid-cols-2 gap-4 lg:grid-cols-5">
        <StatCard label="Files indexed · 24 h" :value="summary.counts.indexed.toLocaleString()" />
        <StatCard label="Deleted · 24 h" :value="summary.counts.deleted.toLocaleString()" />
        <StatCard label="Skipped · 24 h" :value="summary.counts.skipped.toLocaleString()" />
        <StatCard label="Failed · 24 h" :value="summary.counts.failed.toLocaleString()" :tone="summary.counts.failed ? 'warning' : 'default'" />
        <StatCard label="Files in reindex jobs · 24 h" :value="summary.job_files.toLocaleString()" />
      </div>

      <div class="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Last activity per channel</CardTitle>
            <CardDescription>A channel that stopped sending is the first thing to check when files look stale.</CardDescription>
          </CardHeader>
          <CardContent class="space-y-3">
            <ul class="divide-y">
              <li v-for="c in channelRows" :key="c.channel" class="flex items-baseline justify-between gap-4 py-2">
                <span class="font-medium">{{ channelLabel(c.channel) }}</span>
                <span v-if="c.activity.tone === 'never'" class="text-right text-sm text-muted-foreground">{{ c.activity.text }}</span>
                <span v-else class="min-w-0 truncate text-right text-sm text-muted-foreground" :title="c.last_file ?? ''">
                  <span :class="c.activity.tone === 'stale' && 'font-medium text-warning'">{{ c.activity.text }}</span>
                  <span v-if="c.activity.tone === 'stale'" class="sr-only"> (silent for over a day)</span>
                  <template v-if="c.last_file"> · <span class="font-mono text-xs">{{ shortPath(c.last_file) }}</span></template>
                </span>
              </li>
            </ul>
            <p class="text-xs text-muted-foreground">
              The sender declares its channel (<code>X-Mnemos-Client</code> header): a hint, not proof.
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Connector syncs</CardTitle>
            <CardDescription>What each source last did. Details and errors on the <NuxtLink to="/connectors" class="underline">Connectors</NuxtLink> page.</CardDescription>
          </CardHeader>
          <CardContent class="space-y-3">
            <div class="space-y-0.5 text-sm" role="status">
              <p :class="serviceTone">{{ service.text }}.</p>
              <p v-if="waiting" :class="service.health === 'alive' ? 'text-muted-foreground' : 'text-warning'">{{ waiting }}.</p>
            </div>
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
                  <TableHead class="hidden sm:table-cell">State</TableHead>
                  <TableHead class="hidden w-48 sm:table-cell">Progress</TableHead>
                  <TableHead class="hidden md:table-cell">By</TableHead>
                  <TableHead class="hidden text-right md:table-cell">Chunks</TableHead>
                  <TableHead class="hidden pr-6 text-right md:table-cell">Failed</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                <template v-for="j in jobs.items" :key="j.id">
                  <TableRow class="cursor-pointer" @click="toggleJob(j.id)">
                    <TableCell class="pl-5 whitespace-nowrap text-muted-foreground">
                      <button
                        type="button"
                        class="flex items-center gap-1 rounded-md px-1 outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50"
                        :aria-expanded="openJob === j.id"
                        :aria-controls="`job-${j.id}`"
                        @click.stop="toggleJob(j.id)"
                      >
                        <ChevronRightIcon class="size-4 shrink-0 transition-transform" :class="openJob === j.id && 'rotate-90'" aria-hidden="true" />
                        <span class="sr-only">Details of the reindex started</span>
                        {{ fmtTime(j.started_at) }}
                      </button>
                    </TableCell>
                    <TableCell class="max-w-[50vw] sm:max-w-sm">
                      <p>{{ j.collection }}</p>
                      <p class="truncate font-mono text-xs text-muted-foreground" :title="j.path">{{ shortPath(j.path) }}</p>
                      <p class="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground tabular-nums sm:hidden">
                        <Badge :variant="jobBadge(j).variant" :class="jobBadge(j).class">{{ jobState(j) }}</Badge>
                        {{ j.processed.toLocaleString() }} / {{ files(j.total) }}
                      </p>
                    </TableCell>
                    <TableCell class="hidden sm:table-cell">
                      <Badge :variant="jobBadge(j).variant" :class="jobBadge(j).class">{{ jobState(j) }}</Badge>
                    </TableCell>
                    <TableCell class="hidden sm:table-cell">
                      <div
                        class="h-1.5 w-full overflow-hidden rounded-full bg-muted"
                        role="progressbar"
                        :aria-label="`Reindex of ${j.collection}`"
                        :aria-valuenow="percent(j)"
                        :aria-valuetext="`${j.processed.toLocaleString()} of ${files(j.total)}`"
                        aria-valuemin="0"
                        aria-valuemax="100"
                      >
                        <div class="h-full rounded-full bg-primary transition-all" :style="{ width: `${percent(j)}%` }" />
                      </div>
                      <p class="mt-1 text-xs text-muted-foreground tabular-nums" aria-hidden="true">{{ j.processed.toLocaleString() }} / {{ files(j.total) }}</p>
                    </TableCell>
                    <TableCell class="hidden whitespace-nowrap md:table-cell">{{ j.actor }} <Badge variant="outline" class="ml-1 text-[11px]">{{ channelLabel(j.channel) }}</Badge></TableCell>
                    <TableCell class="hidden text-right tabular-nums md:table-cell">{{ j.chunks.toLocaleString() }}</TableCell>
                    <TableCell class="hidden pr-6 text-right tabular-nums md:table-cell" :class="j.failed > 0 && 'text-destructive'">{{ j.failed }}</TableCell>
                  </TableRow>
                  <TableRow v-if="openJob === j.id" :id="`job-${j.id}`">
                    <TableCell colspan="7" class="bg-muted/30 px-6 text-xs whitespace-normal">
                      <p class="text-muted-foreground md:hidden">
                        By {{ j.actor }} ({{ channelLabel(j.channel) }}) · {{ j.chunks.toLocaleString() }} chunks ·
                        <span :class="j.failed > 0 && 'text-destructive'">{{ files(j.failed) }} failed</span>
                      </p>
                      <p class="text-muted-foreground">
                        {{ j.full ? 'Whole tree' : 'One file' }} · {{ files(j.empty) }} without chunks ·
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
                <SelectItem v-for="c in CHANNELS" :key="c.value" :value="c.value">{{ c.label }}</SelectItem>
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
                  <TableHead class="pr-6 text-right">Duration</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                <TableRow v-for="e in rows" :key="e.id" :class="(e.op === 'failed' || e.op === 'refused') && 'bg-destructive/5'">
                  <TableCell class="pl-6 whitespace-nowrap text-muted-foreground">{{ fmtTime(e.at) }}</TableCell>
                  <TableCell><Badge :variant="opVariant(e.op)">{{ opLabel(e.op) }}</Badge></TableCell>
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
          <p class="sr-only" aria-live="polite">{{ announced }}</p>
        </CardContent>
      </Card>
    </template>
  </div>
</template>
