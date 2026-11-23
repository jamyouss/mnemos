<script setup lang="ts">
import type { Component } from 'vue'
import { BookOpenIcon, GitBranchIcon, GlobeIcon, PlugIcon, PlusIcon, RotateCwIcon, TicketIcon, TriangleAlertIcon } from '@lucide/vue'
import { toast } from 'vue-sonner'
import { ApiError, type ConnectorsResponse } from '~/composables/useApi'
import { newSource, toForm, toPayload, type SourceForm, type SourceType } from '~/utils/connectors'
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
        ? 'The ingester resyncs within seconds; restart it for the new interval (docker compose restart ingester).'
        : 'The ingester picks up the change and resyncs within seconds.',
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
        Confluence spaces, Jira projects, web sites and git repositories the ingester
        (<code class="text-xs">mnemos ingest serve</code>) keeps in sync inside <code class="text-xs">mnemos_knowledge</code>
        or <code class="text-xs">mnemos_code</code>. Stored in the database; the ingester checks the server every few
        seconds and resyncs when they change. Tokens stay in the ingester's environment.
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
        <Button variant="outline" size="sm" @click="refresh()"><RotateCwIcon /> Reload</Button>
      </template>
    </PageHeader>

    <AdminOnly v-if="me && !readable" :me="me" what="The connector configuration" />

    <EmptyState v-else-if="error" title="Could not load the connectors" :detail="error.message" />

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
        <AlertTitle>The ingester rejects this configuration</AlertTitle>
        <AlertDescription>
          <ul class="list-disc space-y-0.5 pl-4 font-mono text-xs">
            <li v-for="p in data.problems" :key="p">{{ p }}</li>
          </ul>
          <p class="mt-2">Until it is fixed, a running ingester keeps its previous configuration. Saving replaces it with this form.</p>
        </AlertDescription>
      </Alert>

      <Card>
        <CardHeader>
          <CardTitle>Schedule</CardTitle>
          <CardDescription>
            Resync period after the start-up sync: <code>45s</code>, <code>30m</code>, <code>6h</code>, <code>1d</code>. Blank: only
            at start-up, when the sources change here and on SIGHUP. A new interval needs an ingester restart.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Input v-model="interval" class="w-40 font-mono" placeholder="6h" aria-label="Sync interval" :disabled="!editable" />
        </CardContent>
      </Card>

      <ConnectorSourceCard
        v-for="(s, i) in sources"
        :key="s.id"
        v-model="sources[i]!"
        :editable="editable"
        :indexed="indexed ?? null"
        @remove="remove(s.id)"
      />

      <EmptyState
        v-if="!sources.length"
        :icon="PlugIcon"
        title="No source declared"
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
          <span class="text-sm">Unsaved changes</span>
          <Button variant="ghost" size="sm" @click="load(data)">Discard</Button>
          <Button size="sm" :disabled="saving" @click="save">{{ saving ? 'Saving…' : 'Save' }}</Button>
        </div>
      </div>
    </Transition>
  </div>
</template>
