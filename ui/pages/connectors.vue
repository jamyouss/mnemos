<script setup lang="ts">
import type { Component } from 'vue'
import { BookOpenIcon, GitBranchIcon, GlobeIcon, PlugIcon, PlusIcon, RefreshCcwDotIcon, RefreshCwIcon, TicketIcon, TriangleAlertIcon } from '@lucide/vue'
import { useIntervalFn } from '@vueuse/core'
import { toast } from 'vue-sonner'
import { ApiError, type ConnectorsResponse, type ConnectorSyncState } from '~/composables/useApi'
import {
  newSource, pendingRequests, sourceName, syncPending, syncStalled, toForm, toPayload, type SourceForm, type SourceType,
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

// Sync requests and how far the sync service picked them up. Kept apart from
// `data`: polling them must not reset a form being edited.
const sync = ref<ConnectorSyncState | null>(null)
watch(data, (cfg) => { if (cfg) sync.value = cfg.sync }, { immediate: true })
const savedNames = computed(() => new Set((data.value?.sources ?? []).map(sourceName)))
const pendingAll = computed(() => pendingRequests(sync.value).some((r) => r.sources === null))
// Advanced by the poller, which only runs while something waits.
const now = ref(Date.now())
const stalled = computed(() => syncStalled(sync.value, now.value))
// A sync runs the saved configuration, never the form.
const syncBlocked = computed(() => (dirty.value ? 'Save or discard your changes first: a sync runs the saved configuration.' : ''))

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

// Poll only while a request waits for the sync service.
const poller = useIntervalFn(async () => {
  now.value = Date.now()
  const before = sync.value?.acked_id ?? 0
  try {
    sync.value = await api.connectorSync()
  } catch {
    return // the next tick tries again
  }
  if (sync.value.acked_id > before) {
    toast.success('The sync service started the sync', { description: 'Chunk counts grow as documents land: Refresh to see them.' })
    refreshNuxtData('connector-tags')
  }
}, 3000, { immediate: false })
watch(() => pendingRequests(sync.value).length > 0, (waiting) => (waiting ? poller.resume() : poller.pause()), { immediate: true })

const addable: { type: SourceType; label: string; icon: Component }[] = [
  { type: 'confluence', label: 'Confluence spaces', icon: BookOpenIcon },
  { type: 'jira', label: 'Jira projects', icon: TicketIcon },
  { type: 'web', label: 'Web site', icon: GlobeIcon },
  { type: 'git', label: 'Git repository', icon: GitBranchIcon },
]

function add(type: SourceType) {
  sources.value.push(newSource(type))
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
          :disabled="!!syncBlocked || pendingAll"
          :title="syncBlocked || undefined"
          @click="requestSync()"
        >
          <RefreshCcwDotIcon :class="pendingAll && 'animate-spin'" /> Sync all
        </Button>
        <Button variant="outline" size="sm" @click="refresh()"><RefreshCwIcon /> Refresh</Button>
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

      <Alert v-if="stalled">
        <TriangleAlertIcon />
        <AlertTitle>The sync service has not picked up the request</AlertTitle>
        <AlertDescription>
          It reads requests every few seconds while <code>mnemos ingest serve</code> runs against this server. Check that
          it is running and that its key can read the connectors.
        </AlertDescription>
      </Alert>

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

      <ConnectorSourceCard
        v-for="(s, i) in sources"
        :key="s.id"
        v-model="sources[i]!"
        :editable="editable"
        :indexed="indexed"
        :saved="savedNames.has(sourceName(s))"
        :pending="syncPending(sync, sourceName(s))"
        :sync-blocked="syncBlocked"
        @remove="remove(s.id)"
        @sync="requestSync([sourceName(s)])"
      />

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
          <span class="text-sm" role="status" aria-live="polite">Unsaved changes</span>
          <Button variant="ghost" size="sm" @click="load(data)">Discard</Button>
          <Button size="sm" :disabled="saving" @click="save">{{ saving ? 'Saving…' : 'Save' }}</Button>
        </div>
      </div>
    </Transition>
  </div>
</template>
