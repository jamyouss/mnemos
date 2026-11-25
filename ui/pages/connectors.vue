<script setup lang="ts">
import type { Component } from 'vue'
import { BookOpenIcon, GitBranchIcon, GlobeIcon, PlugIcon, PlusIcon, RefreshCcwDotIcon, RefreshCwIcon, SearchXIcon, TicketIcon, TriangleAlertIcon } from '@lucide/vue'
import { useIntervalFn } from '@vueuse/core'
import { toast } from 'vue-sonner'
import { ApiError, type ConnectorsResponse, type ConnectorSyncState } from '~/composables/useApi'
import {
  ago, newSource, oldestPending, pendingRequests, serviceHealth, sourceMatches, sourceName, syncStalled, toForm, toPayload, waitingLabel,
  type SourceForm, type SourceType,
} from '~/utils/connectors'
import { canReadSetup, isAdmin } from '~/utils/tenants'

definePageMeta({ title: 'Connectors' })
useHead({ title: 'Connectors' })

const api = useApi()
const { data: me } = await useWhoami()
const admin = computed(() => isAdmin(me.value))
const readable = computed(() => canReadSetup(me.value))
const { data, error, refresh } = await useAsyncData('connectors', () => api.connectors(), { immediate: readable.value })
// Chunks per sync tag: whether a source has delivered anything yet. Git
// sources mostly land in mnemos_code. A failure only hides the counts, it
// must not block editing.
const { data: indexed } = await useAsyncData('connector-tags', async () => {
  const results = await Promise.allSettled(
    ['mnemos_knowledge', 'mnemos_code'].map((collection) => api.facets(collection, 'tags', 1000)),
  )
  const counts: Record<string, number> = {}
  for (const r of results) {
    if (r.status === 'fulfilled') for (const c of r.value.counts) counts[c.value] = (counts[c.value] ?? 0) + c.count
  }
  return results.some((r) => r.status === 'fulfilled') ? counts : null
})

const interval = ref('')
const sources = ref<SourceForm[]>([])
// Problems of the last rejected save; the stored configuration's own come with `data`.
const rejected = ref<string[]>([])
const saving = ref(false)

function load(cfg: ConnectorsResponse | null | undefined) {
  if (!cfg) return
  interval.value = cfg.interval ?? ''
  sources.value = cfg.sources.map(toForm)
  rejected.value = []
}
watch(data, load, { immediate: true })

// Compared in payload form, so whitespace or an explicit `deployment: auto`
// in the stored sources does not count as a change.
const serialize = (i: string, list: SourceForm[]) => JSON.stringify({ interval: i.trim(), sources: list.map(toPayload) })
const savedState = computed(() => (data.value ? serialize(data.value.interval ?? '', data.value.sources.map(toForm)) : ''))
const dirty = computed(() => !!data.value && serialize(interval.value, sources.value) !== savedState.value)
const editable = computed(() => admin.value && (data.value?.writable ?? false))

// Sync requests, what the sync service reported and when it was last heard
// from. Kept apart from `data`: polling them must not reset a form being edited.
const sync = ref<ConnectorSyncState | null>(null)
watch(data, (cfg) => { if (cfg) sync.value = cfg.sync }, { immediate: true })
const savedNames = computed(() => new Set((data.value?.sources ?? []).map(sourceName)))
const allWaiting = computed(() => oldestPending(sync.value))
const anyRunning = computed(() => Object.values(sync.value?.runs ?? {}).some((r) => r.state === 'running'))
const now = ref(Date.now())
const stalled = computed(() => syncStalled(sync.value, now.value))
const health = computed(() => serviceHealth(sync.value?.service_seen_at, now.value))

async function requestSync(names?: string[]) {
  try {
    sync.value = await api.requestConnectorSync(names)
    toast.success(names ? `Sync of ${names[0]} requested` : 'Sync of every source requested', {
      description: 'The sync service picks it up within seconds.',
    })
  } catch (e) {
    toast.error('Could not request a sync', { description: e instanceof Error ? e.message : String(e) })
  }
}

async function poll() {
  now.value = Date.now()
  const before = sync.value
  try {
    sync.value = await api.connectorSync()
  } catch {
    return // the next tick tries again
  }
  if (before && sync.value.acked_id > before.acked_id) toast.info('The sync service picked up the request')
  // Counts grow while documents land: show them moving rather than only at the end.
  if (anyRunning.value) refreshNuxtData('connector-tags')
  for (const [name, run] of Object.entries(sync.value.runs)) {
    // A finish time that moved: a sync ended since the last poll, however short it was.
    if (!before || run.state === 'running' || run.finished_at === before.runs[name]?.finished_at) continue
    if (run.state === 'ok') toast.success(`${name} synced`)
    else toast.error(`Sync of ${name} failed`, { description: run.results.find((r) => r.error)?.error?.split('\n')[0] })
    refreshNuxtData('connector-tags')
  }
}
// Every 3 s while a request waits or a sync runs, every 30 s otherwise: enough to see the service stop.
useIntervalFn(poll, () => (pendingRequests(sync.value).length || anyRunning.value ? 3000 : 30_000))

// Reloading `data` resets the form: with unsaved edits, refresh only the sync status and the counts.
const refreshing = ref(false)
async function refreshPage() {
  refreshing.value = true
  try {
    now.value = Date.now()
    await Promise.all([dirty.value ? poll() : refresh(), refreshNuxtData('connector-tags')])
  } finally {
    refreshing.value = false
  }
}

const addable: { type: SourceType; label: string; icon: Component }[] = [
  { type: 'confluence', label: 'Confluence spaces', icon: BookOpenIcon },
  { type: 'jira', label: 'Jira projects', icon: TicketIcon },
  { type: 'web', label: 'Web site', icon: GlobeIcon },
  { type: 'git', label: 'Git repository', icon: GitBranchIcon },
]

const query = ref('')
const page = ref(1)
const pageSize = ref(10)
const matching = computed(() => sources.value.filter((s) => sourceMatches(s, query.value)))
const shown = computed(() => matching.value.slice((page.value - 1) * pageSize.value, page.value * pageSize.value))
watch([query, pageSize], () => { page.value = 1 })
// A removed source can empty the last page: stay on the last one that has sources.
watch(() => matching.value.length, (n) => { page.value = Math.min(page.value, Math.max(1, Math.ceil(n / pageSize.value))) })

function update(id: number, value: SourceForm) {
  const i = sources.value.findIndex((s) => s.id === id)
  if (i >= 0) sources.value[i] = value
}

async function add(type: SourceType) {
  sources.value.push(newSource(type))
  // Show the new card: no filter hides it, and its page is the last one.
  query.value = ''
  await nextTick()
  page.value = Math.ceil(sources.value.length / pageSize.value)
}

function remove(id: number) {
  sources.value = sources.value.filter((s) => s.id !== id)
}

async function save() {
  if (!data.value) return
  saving.value = true
  const intervalChanged = interval.value.trim() !== (data.value.interval ?? '')
  try {
    data.value = await api.saveConnectors({
      interval: interval.value.trim(),
      sources: sources.value.map(toPayload),
      revision: data.value.revision,
    })
    toast.success('Connectors saved', {
      description: intervalChanged
        ? 'Sources resync within seconds; the new interval applies after the sync service restarts.'
        : 'The sync service picks up the change and resyncs within seconds.',
    })
  } catch (e) {
    if (e instanceof ApiError && e.body?.problems) rejected.value = e.body.problems
    toastSaveError(e, refresh)
  } finally {
    saving.value = false
  }
}

onBeforeRouteLeave(() => {
  if (dirty.value && !window.confirm('Discard unsaved connector changes?')) return false
})
</script>

<template>
  <div class="space-y-6 pb-20">
    <PageHeader title="Connectors">
      <template #description>
        Confluence spaces, Jira projects, web sites and git repositories kept in sync with the index. Tokens stay on
        the sync service, never here.
      </template>
      <template #actions>
        <DropdownMenu v-if="editable">
          <DropdownMenuTrigger as-child>
            <Button size="sm"><PlusIcon /> Add source</Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem v-for="a in addable" :key="a.type" @select="add(a.type)">
              <component :is="a.icon" /> {{ a.label }}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        <Button
          v-if="data?.sources.length"
          variant="outline"
          size="sm"
          :disabled="dirty || !!allWaiting"
          @click="requestSync()"
        >
          <RefreshCcwDotIcon :class="anyRunning && 'animate-spin'" /> Sync all
        </Button>
        <Button variant="outline" size="sm" :disabled="refreshing" @click="refreshPage">
          <RefreshCwIcon :class="refreshing && 'animate-spin'" /> Refresh
        </Button>
      </template>
    </PageHeader>

    <AdminOnly v-if="me && !readable" :me="me" what="The connector configuration" />

    <EmptyState v-else-if="error" tone="error" title="Could not load the connectors" :detail="error.message" />

    <template v-else-if="data">
      <ReadOnlyAlert :writable="data.writable" :me="me" />

      <Alert v-if="rejected.length" variant="destructive">
        <TriangleAlertIcon />
        <AlertTitle>Nothing was saved</AlertTitle>
        <AlertDescription>
          <ul class="list-disc space-y-0.5 pl-4 font-mono text-xs">
            <li v-for="p in rejected" :key="p">{{ p }}</li>
          </ul>
        </AlertDescription>
      </Alert>

      <Alert v-else-if="data.problems.length" variant="destructive">
        <TriangleAlertIcon />
        <AlertTitle>The sync service rejects this configuration</AlertTitle>
        <AlertDescription>
          <ul class="list-disc space-y-0.5 pl-4 font-mono text-xs">
            <li v-for="p in data.problems" :key="p">{{ p }}</li>
          </ul>
          <p class="mt-2">Until it is fixed, the sync service keeps its previous configuration. Saving replaces it with this form.</p>
        </AlertDescription>
      </Alert>

      <Alert v-if="data.sources.length && health === 'never'">
        <TriangleAlertIcon />
        <AlertTitle>No sync service has reported to this server</AlertTitle>
        <AlertDescription>
          Sources sync only while <code>mnemos ingest serve</code> runs against this server, with
          <code>MNEMOS_API_KEY</code> set to a key that writes every tag. With Docker Compose:
          <code>docker compose --profile connectors up -d ingester</code>. Sync requests wait until then.
        </AlertDescription>
      </Alert>

      <Alert v-else-if="health === 'silent'" variant="destructive">
        <TriangleAlertIcon />
        <AlertTitle>The sync service stopped reporting</AlertTitle>
        <AlertDescription>
          Last heard from {{ ago(sync!.service_seen_at!, now) }}. Nothing syncs until it is back: check that
          <code>mnemos ingest serve</code> is running and reaches this server.
        </AlertDescription>
      </Alert>

      <Alert v-else-if="stalled">
        <TriangleAlertIcon />
        <AlertTitle>The sync service has not picked up the request</AlertTitle>
        <AlertDescription>
          It reads requests every few seconds. Check that its key can read the connectors
          (<code>admin</code> or <code>write: "*"</code>).
        </AlertDescription>
      </Alert>

      <p v-else-if="health === 'alive' && sync?.service_seen_at" class="text-xs text-muted-foreground" role="status">
        Sync service running, last heard from {{ ago(sync.service_seen_at, now) }}.
      </p>

      <p v-if="allWaiting" class="text-xs" :class="health === 'alive' ? 'text-muted-foreground' : 'text-warning'" role="status">
        Sync of every source: {{ waitingLabel(allWaiting, now) }}.
      </p>

      <Card>
        <CardHeader>
          <CardTitle>Schedule</CardTitle>
          <CardDescription>
            How often sources are resynced: <code>45s</code>, <code>30m</code>, <code>6h</code>, <code>1d</code>. Leave blank
            to sync only when sources change or on request. A new interval needs a restart of the sync service.
          </CardDescription>
        </CardHeader>
        <CardContent class="space-y-1.5">
          <Label for="interval">Resync interval</Label>
          <Input id="interval" v-model="interval" class="w-40 font-mono" placeholder="6h" :disabled="!editable" />
        </CardContent>
      </Card>

      <div v-if="sources.length > 1" class="flex flex-wrap items-center gap-3">
        <Input
          v-model="query"
          type="search"
          placeholder="Search by name, URL, space, project or tag…"
          class="h-9 min-w-56 flex-1"
          aria-label="Search sources"
        />
        <span v-if="query.trim()" class="text-sm text-muted-foreground tabular-nums" aria-live="polite">
          {{ matching.length }} of {{ sources.length }} sources
        </span>
      </div>

      <ConnectorSourceCard
        v-for="s in shown"
        :key="s.id"
        :model-value="s"
        :editable="editable"
        :indexed="indexed"
        :saved="savedNames.has(sourceName(s))"
        :waiting="oldestPending(sync, sourceName(s))"
        :service-down="health !== 'alive'"
        :sync-blocked="dirty"
        :run="sync?.runs[sourceName(s)]"
        :now="now"
        @update:model-value="update(s.id, $event)"
        @remove="remove(s.id)"
        @sync="requestSync([sourceName(s)])"
      />

      <EmptyState
        v-if="sources.length && !matching.length"
        :icon="SearchXIcon"
        title="No source matches"
        :detail="`Nothing matches “${query.trim()}”. Search looks at names, URLs, spaces, projects, branches and tags.`"
      />

      <ListPager v-model:page="page" v-model:page-size="pageSize" :total="matching.length" :sizes="[10, 15, 30, 50, 100]" />

      <EmptyState
        v-if="!sources.length"
        :icon="PlugIcon"
        title="No sources yet"
        :detail="editable
          ? 'Add a Confluence space, a Jira project, a web site or a git repository.'
          : 'Nothing is synced. An admin adds sources here once editing is enabled.'"
      />

      <p v-if="sources.length" class="text-xs text-muted-foreground">
        Removing a source stops its syncs; what it already indexed stays in the index.
      </p>
    </template>

    <Transition
      enter-active-class="transition duration-200 ease-out"
      enter-from-class="translate-y-4 opacity-0"
      leave-active-class="transition duration-150 ease-in"
      leave-to-class="translate-y-4 opacity-0"
    >
      <div v-if="dirty" class="fixed inset-x-0 bottom-6 z-20 flex justify-center px-4">
        <div class="flex items-center gap-3 rounded-xl border bg-popover px-4 py-2.5 shadow-lg">
          <div class="min-w-0">
            <p class="text-sm" role="status" aria-live="polite">Unsaved changes</p>
            <p v-if="data?.sources.length" class="text-xs text-muted-foreground">Sync is off until you save or discard: it runs the saved configuration.</p>
          </div>
          <Button variant="ghost" size="sm" @click="load(data)">Discard</Button>
          <Button size="sm" :disabled="saving" @click="save">{{ saving ? 'Saving…' : 'Save' }}</Button>
        </div>
      </div>
    </Transition>
  </div>
</template>
