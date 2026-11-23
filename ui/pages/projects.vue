<script setup lang="ts">
import { FolderTreeIcon, PlusIcon, RotateCwIcon, Trash2Icon, TriangleAlertIcon } from '@lucide/vue'
import { toast } from 'vue-sonner'
import { ApiError, type ProjectsResponse } from '~/composables/useApi'
import { newRow, problemsByRow, toPathPayload, toRowForm, type ProjectRowForm } from '~/utils/projects'
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

function add() {
  rows.value.push(newRow())
}

function remove(id: number) {
  rows.value = rows.value.filter((r) => r.id !== id)
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

      <Card v-if="rows.length">
        <CardContent class="space-y-3">
          <div class="hidden grid-cols-[minmax(0,1.2fr)_minmax(0,1.5fr)_minmax(0,1fr)_minmax(0,1fr)_2.25rem] gap-3 text-xs font-medium text-muted-foreground md:grid">
            <span>Path prefix</span>
            <span>Tags (first = primary)</span>
            <span>Ignored directories</span>
            <span>Ignored extensions</span>
            <span />
          </div>
          <div v-for="row in rows" :key="row.id" class="space-y-1.5">
            <div
              class="grid gap-3 md:grid-cols-[minmax(0,1.2fr)_minmax(0,1.5fr)_minmax(0,1fr)_minmax(0,1fr)_2.25rem] md:items-center"
            >
              <Input
                v-model="row.prefix"
                class="font-mono"
                placeholder="acme/webshop/"
                aria-label="Path prefix"
                :aria-invalid="problems.byId.has(row.id) || undefined"
                :disabled="!editable"
              />
              <FacetCombobox v-if="editable" :id="`tags-${row.id}`" v-model="row.tags" :options="tagOptions" multiple placeholder="Pick tags…" />
              <div v-else class="flex min-h-9 flex-wrap items-center gap-1">
                <Badge v-for="(t, i) in splitFilterList(row.tags)" :key="t" :variant="i === 0 ? 'default' : 'secondary'" class="font-mono text-[11px]">{{ t }}</Badge>
              </div>
              <Input v-model="row.excludeDirs" class="font-mono" placeholder="exports, fixtures" aria-label="Ignored directories" :disabled="!editable" />
              <Input v-model="row.excludeExts" class="font-mono" placeholder=".csv, .min.js" aria-label="Ignored extensions" :disabled="!editable" />
              <Button v-if="editable" variant="ghost" size="icon" class="size-9" :aria-label="`Remove ${row.prefix || 'row'}`" @click="remove(row.id)">
                <Trash2Icon />
              </Button>
            </div>
            <ul v-if="problems.byId.has(row.id)" class="space-y-0.5 text-xs text-destructive">
              <li v-for="p in problems.byId.get(row.id)" :key="p">{{ p }}</li>
            </ul>
          </div>
        </CardContent>
      </Card>

      <EmptyState
        v-else
        :icon="FolderTreeIcon"
        title="No prefix mapped"
        detail="Without a mapping, a file gets its cumulative path segments as tags: foo/bar/file.go → foo, foo/bar."
      >
        <Button v-if="editable" size="sm" @click="add"><PlusIcon /> Add prefix</Button>
      </EmptyState>

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
