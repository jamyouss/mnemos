<script setup lang="ts">
import { FolderTreeIcon, PencilIcon, PlusIcon, RotateCwIcon, SearchIcon, Trash2Icon, TriangleAlertIcon } from '@lucide/vue'
import { toast } from 'vue-sonner'
import { ApiError, type ProjectsResponse } from '~/composables/useApi'
import { groupRows, newRow, problemsByRow, toPathPayload, toRowForm, type ProjectRowForm } from '~/utils/projects'
import { splitFilterList } from '~/utils/playground'
import { canReadSetup, isAdmin } from '~/utils/tenants'

definePageMeta({ title: 'Projects' })
useHead({ title: 'Projects' })

const api = useApi()
const { data: me } = await useWhoami()
const admin = computed(() => isAdmin(me.value))
const readable = computed(() => canReadSetup(me.value))
const { data, error, refresh } = await useAsyncData('projects', () => api.projects(), { immediate: readable.value })
// Suggestions only: a tag nothing carries yet can still be typed.
const tagFacets = useAsyncData('projects-tags', () => api.facets('mnemos_code', 'tags', 500), { lazy: true })
const tagOptions = computed(() => tagFacets.data.value?.counts ?? [])

const rows = ref<ProjectRowForm[]>([])
const saving = ref(false)

interface ShownProblems {
  /** Keyed by row id, so adding or removing rows afterwards does not move them. */
  byId: Map<number, string[]>
  general: string[]
  /** From a refused save, rather than the stored mapping. */
  rejected: boolean
}
const problems = ref<ShownProblems>({ byId: new Map(), general: [], rejected: false })

// `problems` index the list that was validated, which is `list` in its current order.
function attach(found: string[], list: ProjectRowForm[], rejected: boolean): ShownProblems {
  const { byRow, general } = problemsByRow(found)
  const byId = new Map<number, string[]>()
  for (const [index, messages] of byRow) {
    const row = list[index]
    if (row) byId.set(row.id, messages)
    else general.push(...messages)
  }
  return { byId, general, rejected }
}

function load(res: ProjectsResponse | null | undefined) {
  if (!res) return
  rows.value = res.paths.map(toRowForm)
  problems.value = attach(res.problems, rows.value, false)
}
watch(data, load, { immediate: true })

const serialize = (list: ProjectRowForm[]) => JSON.stringify(list.map(toPathPayload))
const savedState = computed(() => (data.value ? serialize(data.value.paths.map(toRowForm)) : ''))
const dirty = computed(() => !!data.value && serialize(rows.value) !== savedState.value)
const editable = computed(() => admin.value && (data.value?.writable ?? false))

const query = ref('')
const groups = computed(() => groupRows(rows.value, query.value))
const shown = computed(() => groups.value.reduce((n, g) => n + g.rows.length, 0))

// The editor works on the draft row itself: the save bar persists every change at once.
const editingId = ref<number | null>(null)
const editing = computed(() => rows.value.find((r) => r.id === editingId.value) ?? null)
const editorOpen = computed({
  get: () => editing.value !== null,
  set: (open) => { if (!open) closeEditor() },
})

function openEditor(id: number) {
  editingId.value = id
}

function closeEditor() {
  const row = editing.value
  // A new row left blank was a misclick, not a prefix.
  if (row && !row.prefix.trim() && !row.tags.trim()) remove(row.id)
  editingId.value = null
}

function add() {
  const row = newRow()
  rows.value.push(row)
  openEditor(row.id)
}

function remove(id: number) {
  rows.value = rows.value.filter((r) => r.id !== id)
  if (editingId.value === id) editingId.value = null
}

async function save() {
  if (!data.value) return
  saving.value = true
  const sent = [...rows.value]
  try {
    data.value = await api.saveProjects({ paths: sent.map(toPathPayload), revision: data.value.revision })
    toast.success('Projects saved', {
      description: 'Every server instance applies the mapping within seconds. Files already indexed keep their tags until reindexed.',
    })
  } catch (e) {
    if (e instanceof ApiError && e.body?.problems) problems.value = attach(e.body.problems, sent, true)
    toastSaveError(e, refresh)
  } finally {
    saving.value = false
  }
}

onBeforeRouteLeave(() => {
  if (dirty.value && !window.confirm('Discard unsaved project changes?')) return false
})
</script>

<template>
  <div class="space-y-6 pb-20">
    <PageHeader title="Projects">
      <template #description>
        Which tags each indexed file gets, by path prefix relative to the codebase root; the longest matching prefix wins
        and its first tag is the project's primary label. Tags scope searches and tenants. Stored in the database and
        applied by every server instance within seconds.
      </template>
      <template #actions>
        <Button v-if="editable" size="sm" @click="add"><PlusIcon /> Add prefix</Button>
        <Button variant="outline" size="sm" @click="refresh()"><RotateCwIcon /> Reload</Button>
      </template>
    </PageHeader>

    <AdminOnly v-if="me && !readable" :me="me" what="The project mapping" />

    <EmptyState v-else-if="error" title="Could not load the projects" :detail="error.message" />

    <template v-else-if="data">
      <ReadOnlyAlert :writable="data.writable" :me="me" />

      <Alert v-if="problems.general.length || problems.byId.size" variant="destructive">
        <TriangleAlertIcon />
        <AlertTitle>{{ problems.rejected ? 'Nothing was saved' : 'The stored mapping has problems' }}</AlertTitle>
        <AlertDescription>
          <ul v-if="problems.general.length" class="list-disc space-y-0.5 pl-4 font-mono text-xs">
            <li v-for="p in problems.general" :key="p">{{ p }}</li>
          </ul>
          <p v-if="problems.byId.size">The rows marked below need fixing.</p>
        </AlertDescription>
      </Alert>

      <template v-if="rows.length">
        <div class="flex flex-wrap items-center gap-3">
          <div class="relative w-full max-w-sm">
            <SearchIcon class="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input v-model="query" type="search" placeholder="Filter by path or tag…" class="pl-8" aria-label="Filter prefixes" />
          </div>
          <span class="text-sm text-muted-foreground tabular-nums">
            {{ query ? `${shown} of ${rows.length}` : rows.length }} prefix{{ rows.length > 1 ? 'es' : '' }}
          </span>
        </div>

        <EmptyState v-if="!groups.length" :icon="SearchIcon" title="No prefix matches" :detail="`Nothing in the mapping contains “${query}”.`" />

        <Card v-for="group in groups" :key="group.key" class="gap-0 overflow-hidden py-0">
          <div class="flex items-center justify-between border-b bg-muted/40 px-4 py-2.5">
            <span class="font-mono text-sm font-medium">{{ group.key || 'Other prefixes' }}</span>
            <span class="text-xs text-muted-foreground tabular-nums">{{ group.rows.length }}</span>
          </div>
          <ul class="divide-y">
            <li
              v-for="row in group.rows"
              :key="row.id"
              class="group/row flex items-start gap-4 px-4 py-3"
              :class="problems.byId.has(row.id) && 'bg-destructive/5'"
            >
              <div class="min-w-0 flex-1 space-y-1.5">
                <p class="font-mono text-sm break-all">
                  <span class="text-muted-foreground">{{ group.key }}</span>{{ row.prefix.slice(group.key.length) || (row.prefix ? '' : 'new prefix') }}
                </p>
                <div class="flex flex-wrap items-center gap-1">
                  <Badge
                    v-for="(t, i) in splitFilterList(row.tags)"
                    :key="t"
                    :variant="i === 0 ? 'default' : 'secondary'"
                    class="font-mono text-[11px]"
                    :title="i === 0 ? 'Primary tag' : undefined"
                  >{{ t }}</Badge>
                  <span v-if="!row.tags.trim()" class="text-xs text-destructive">no tag</span>
                </div>
                <p v-if="row.excludeDirs.trim() || row.excludeExts.trim()" class="text-xs text-muted-foreground">
                  Ignores <span class="font-mono">{{ [row.excludeDirs, row.excludeExts].filter((s) => s.trim()).join(', ') }}</span>
                </p>
                <ul v-if="problems.byId.has(row.id)" class="space-y-0.5 text-xs text-destructive">
                  <li v-for="p in problems.byId.get(row.id)" :key="p">{{ p }}</li>
                </ul>
              </div>
              <div v-if="editable" class="flex shrink-0 gap-1 opacity-60 transition-opacity group-hover/row:opacity-100 focus-within:opacity-100">
                <Button variant="ghost" size="icon" class="size-8" :aria-label="`Edit ${row.prefix || 'new prefix'}`" @click="openEditor(row.id)">
                  <PencilIcon />
                </Button>
                <Button variant="ghost" size="icon" class="size-8" :aria-label="`Remove ${row.prefix || 'new prefix'}`" @click="remove(row.id)">
                  <Trash2Icon />
                </Button>
              </div>
            </li>
          </ul>
        </Card>
      </template>

      <EmptyState
        v-else
        :icon="FolderTreeIcon"
        title="No prefix mapped"
        detail="Without a mapping, a file gets its cumulative path segments as tags: foo/bar/file.go → foo, foo/bar."
      >
        <Button v-if="editable" size="sm" @click="add"><PlusIcon /> Add prefix</Button>
      </EmptyState>

      <Sheet v-model:open="editorOpen">
        <SheetContent side="right" class="w-full sm:max-w-md">
          <SheetHeader>
            <SheetTitle>{{ editing?.prefix ? 'Edit prefix' : 'New prefix' }}</SheetTitle>
            <SheetDescription>Saved with the other changes from the bar at the bottom of the page.</SheetDescription>
          </SheetHeader>
          <form v-if="editing" class="space-y-5 px-4" @submit.prevent="closeEditor">
            <div class="space-y-1.5">
              <Label for="edit-prefix">Path prefix</Label>
              <Input id="edit-prefix" v-model="editing.prefix" class="font-mono" placeholder="acme/webshop/" autofocus />
              <p class="text-xs text-muted-foreground">Relative to the codebase root. The longest matching prefix wins.</p>
            </div>
            <div class="space-y-1.5">
              <Label for="edit-tags">Tags</Label>
              <FacetCombobox id="edit-tags" v-model="editing.tags" :options="tagOptions" multiple placeholder="Pick tags…" />
              <p class="text-xs text-muted-foreground">The first tag is the project's primary label; tenants are scoped by these tags.</p>
            </div>
            <div class="space-y-1.5">
              <Label for="edit-dirs">Ignored directories</Label>
              <Input id="edit-dirs" v-model="editing.excludeDirs" class="font-mono" placeholder="exports, fixtures" />
            </div>
            <div class="space-y-1.5">
              <Label for="edit-exts">Ignored extensions</Label>
              <Input id="edit-exts" v-model="editing.excludeExts" class="font-mono" placeholder=".csv, .min.js" />
              <p class="text-xs text-muted-foreground">Added to the built-in policy for this prefix only.</p>
            </div>
            <Button type="submit" class="w-full">Done</Button>
          </form>
        </SheetContent>
      </Sheet>

      <p v-if="rows.length" class="text-xs text-muted-foreground">
        Ignore rules add to the built-in policy and hold for every way files arrive (watcher, git hooks, reindex);
        nothing here re-allows what a built-in rule skips. Changing tags does not retag files already indexed: reindex them.
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
