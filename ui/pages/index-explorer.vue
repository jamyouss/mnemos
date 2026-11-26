<script setup lang="ts">
import { ExternalLinkIcon, FileSearchIcon, FolderSyncIcon, RefreshCwIcon, RotateCwIcon, Trash2Icon } from '@lucide/vue'
import { refDebounced } from '@vueuse/core'
import { toast } from 'vue-sonner'
import type { IndexedFile, IndexedFileDetail, PathCheck } from '~/composables/useApi'
import { buttonVariants } from '~/components/ui/button'
import { ago } from '~/utils/connectors'
import { chunkerLabel, displayTags, fileLabel, INDEX_COLLECTIONS, REINDEX_ROOTS, shortPath, skipExplanation, TAGGED_COLLECTIONS } from '~/utils/indexFiles'
import { canWriteAll } from '~/utils/tenants'

// `pages/index.vue` is the dashboard, so this page names its route itself.
definePageMeta({ title: 'Index', path: '/index' })
useHead({ title: 'Index' })

const api = useApi()
const { data: me } = await useWhoami()
const writeAll = computed(() => canWriteAll(me.value))

// ---- Browse ----------------------------------------------------------------
const collection = ref<string>('mnemos_code')
const tag = ref('')
const directory = ref('')
const directoryApplied = refDebounced(directory, 400)
const tagged = computed(() => TAGGED_COLLECTIONS.includes(collection.value))
// Tags are per collection: one picked for code would empty the knowledge list.
watch(collection, () => (tag.value = ''))

const filters = computed(() => ({
  collection: collection.value,
  tag: tagged.value ? tag.value.trim() || undefined : undefined,
  directory: directoryApplied.value.trim() || undefined,
}))

const { data: tagOptions } = useAsyncData(
  'index-tags',
  () => (tagged.value ? api.facets(collection.value, 'tags', 1000).then((r) => r.counts).catch(() => []) : Promise.resolve([])),
  { watch: [collection], lazy: true, default: () => [] },
)

const { data, error, pending, refresh } = await useAsyncData('index-browse', () => api.browseIndex({ ...filters.value, limit: 50 }), {
  watch: [filters],
})
const more = ref<IndexedFile[]>([])
const cursor = ref<string | null>(null)
const now = ref(Date.now())
watch(data, (page) => {
  more.value = []
  cursor.value = page?.next_cursor ?? null
  now.value = Date.now()
}, { immediate: true })
const rows = computed(() => [...(data.value?.items ?? []), ...more.value])
// Two chips keep a row on one line; the rest is counted, every tag stays in the file sheet.
const labelled = computed(() => rows.value.map((f) => ({ ...f, label: fileLabel(f.file_path, f.source_url), chips: displayTags(f.tags, 2) })))
const total = computed(() => data.value?.total ?? 0)
const filtered = computed(() => !!(filters.value.tag || filters.value.directory))
const announced = computed(() => {
  if (!data.value) return ''
  const n = total.value
  return `${n.toLocaleString()} ${n === 1 ? 'file' : 'files'}, ${rows.value.length.toLocaleString()} shown`
})

const loadingMore = ref(false)
async function loadMore() {
  if (cursor.value === null) return
  loadingMore.value = true
  try {
    const page = await api.browseIndex({ ...filters.value, cursor: cursor.value, limit: 50 })
    more.value.push(...page.items)
    cursor.value = page.next_cursor
  } catch (e) {
    toast.error('Could not load more files', { description: (e as Error).message })
  } finally {
    loadingMore.value = false
  }
}

function clearFilters() {
  tag.value = ''
  directory.value = ''
}

const fmtTime = (iso: string) => new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
const fmtMtime = (seconds: number) => new Date(seconds * 1000).toLocaleString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
const baseName = (p: string) => p.split('/').filter(Boolean).pop() ?? p

// ---- File sheet --------------------------------------------------------------
const opened = ref<{ collection: string; file_path: string } | null>(null)
const detail = ref<IndexedFileDetail | null>(null)
const detailError = ref<string | null>(null)
const sheetOpen = computed({ get: () => opened.value !== null, set: (open) => { if (!open) opened.value = null } })

let opening = 0
async function openFile(c: string, filePath: string) {
  const token = ++opening
  opened.value = { collection: c, file_path: filePath }
  detail.value = null
  detailError.value = null
  try {
    const result = await api.indexedFile(c, filePath)
    // A slower answer for a file opened before must not replace this one.
    if (token === opening) detail.value = result
  } catch (e) {
    if (token === opening) detailError.value = (e as Error).message
  }
}

const reindexing = ref(false)
async function reindexFile() {
  if (!detail.value) return
  reindexing.value = true
  try {
    await api.reindex({ collection: detail.value.collection, path: detail.value.file_path, full: false })
    toast.success('Reindex started', {
      description: shortPath(detail.value.file_path),
      action: { label: 'View progress', onClick: () => navigateTo('/indexing') },
    })
  } catch (e) {
    toast.error('Could not start the reindex', { description: (e as Error).message })
  } finally {
    reindexing.value = false
  }
}

const confirmRemove = ref(false)
async function removeFile() {
  const file = detail.value
  if (!file) return
  try {
    await api.deleteIndexedFile(file.collection, file.file_path)
    toast.success('Removed from the index', { description: `${shortPath(file.file_path)} no longer comes back in search.` })
    opened.value = null
    await refresh()
  } catch (e) {
    toast.error('Could not remove the file', { description: (e as Error).message })
  }
}

// ---- Reindex a folder ----------------------------------------------------------
const reindexOpen = ref(false)
const reindexPath = computed(() => {
  const dir = directory.value.trim()
  return dir.startsWith('/') ? dir : undefined
})

// ---- Path tester ---------------------------------------------------------------
const testPath = ref('')
const check = ref<PathCheck | null>(null)
const checking = ref(false)
const checkError = ref<string | null>(null)
async function runCheck() {
  const path = testPath.value.trim()
  if (!path) return
  checking.value = true
  checkError.value = null
  try {
    check.value = await api.checkPath(path)
  } catch (e) {
    check.value = null
    checkError.value = (e as Error).message
  } finally {
    checking.value = false
  }
}
</script>

<template>
  <div class="space-y-6">
    <PageHeader
      title="Index"
      description="What the index holds, file by file: the chunks search returns, their tags and when they were last indexed."
    >
      <template #actions>
        <Button v-if="writeAll" variant="outline" size="sm" @click="reindexOpen = true"><FolderSyncIcon /> Reindex a folder</Button>
        <Button variant="outline" size="sm" :disabled="pending" @click="refresh()">
          <RefreshCwIcon :class="pending && 'animate-spin'" /> Refresh
        </Button>
      </template>
    </PageHeader>

    <Card>
      <CardHeader class="flex flex-row flex-wrap items-center justify-between gap-3">
        <div class="space-y-1">
          <CardTitle>Files</CardTitle>
          <CardDescription v-if="data">{{ total.toLocaleString() }} {{ total === 1 ? 'file' : 'files' }}</CardDescription>
        </div>
        <div class="flex w-full flex-wrap items-center gap-2 sm:w-auto">
          <Select v-model="collection">
            <SelectTrigger size="sm" class="w-full sm:w-44" aria-label="Collection"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem v-for="c in INDEX_COLLECTIONS" :key="c" :value="c">{{ c }}</SelectItem>
            </SelectContent>
          </Select>
          <div v-if="tagged" class="w-full sm:w-52">
            <Label for="index-tag" class="sr-only">Tag</Label>
            <FacetCombobox id="index-tag" v-model="tag" :options="tagOptions" placeholder="Any tag" />
          </div>
          <Input
            v-model="directory"
            type="search"
            :placeholder="REINDEX_ROOTS[collection] ? `Under ${REINDEX_ROOTS[collection]}…` : 'Under directory…'"
            class="h-8 w-full font-mono text-xs sm:w-60"
            aria-label="Only files under this directory (stored path)"
          />
        </div>
      </CardHeader>
      <CardContent class="px-0">
        <EmptyState v-if="error" tone="error" title="Could not list the index" :detail="error.message" class="mx-6" />
        <div v-else-if="rows.length" class="overflow-x-auto" :aria-busy="pending">
          <Table class="md:table-fixed" :class="pending && 'opacity-60 transition-opacity'">
            <TableHeader>
              <TableRow>
                <TableHead class="pl-6">File</TableHead>
                <TableHead class="hidden w-72 md:table-cell">Tags</TableHead>
                <TableHead class="hidden w-28 sm:table-cell">Language</TableHead>
                <TableHead class="hidden w-28 pr-6 text-right sm:table-cell">Last indexed</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              <TableRow v-for="f in labelled" :key="`${f.collection}:${f.file_path}`" class="cursor-pointer" @click="openFile(f.collection, f.file_path)">
                <TableCell class="max-w-[calc(100vw-4rem)] min-w-0 pl-6 whitespace-normal md:max-w-none">
                  <button
                    type="button"
                    class="-mx-1 block max-w-full truncate rounded-md px-1 text-left text-sm font-medium outline-none hover:underline focus-visible:ring-3 focus-visible:ring-ring/50"
                    :title="f.file_path"
                    @click.stop="openFile(f.collection, f.file_path)"
                  >
                    <span class="sr-only">Open </span>{{ f.label.name }}
                  </button>
                  <p v-if="f.label.place" class="mt-0.5 truncate font-mono text-xs text-muted-foreground" :title="f.file_path">{{ f.label.place }}</p>
                  <p class="mt-0.5 truncate text-xs text-muted-foreground md:hidden">
                    <template v-if="f.chips.shown.length">{{ f.chips.shown.join(', ') }}</template>
                    <span class="sm:hidden"><template v-if="f.chips.shown.length"> · </template>{{ f.language ?? '—' }}<template v-if="f.last_indexed_at"> · {{ ago(f.last_indexed_at, now) }}</template></span>
                  </p>
                </TableCell>
                <TableCell class="hidden md:table-cell">
                  <div class="flex min-w-0 items-center gap-1">
                    <Badge
                      v-for="(t, i) in f.chips.shown"
                      :key="t"
                      :variant="i === 0 ? 'secondary' : 'outline'"
                      class="block max-w-full min-w-0 shrink truncate font-mono text-[11px]"
                      :title="t"
                    >{{ t }}</Badge>
                    <Badge v-if="f.chips.more.length" variant="outline" class="shrink-0 font-mono text-[11px] text-muted-foreground" :title="f.chips.more.join(', ')">
                      +{{ f.chips.more.length }}
                    </Badge>
                    <span v-if="!f.chips.shown.length" class="text-muted-foreground">—</span>
                  </div>
                </TableCell>
                <TableCell class="hidden truncate sm:table-cell">{{ f.language ?? '—' }}</TableCell>
                <TableCell class="hidden pr-6 text-right whitespace-nowrap text-muted-foreground sm:table-cell" :title="f.last_indexed_at ? fmtTime(f.last_indexed_at) : undefined">
                  {{ f.last_indexed_at ? ago(f.last_indexed_at, now) : '—' }}
                </TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </div>
        <EmptyState v-else-if="filtered" :icon="FileSearchIcon" title="No file matches" detail="No indexed file has this tag or sits under this directory." class="mx-6">
          <Button variant="outline" size="sm" @click="clearFilters">Clear filters</Button>
        </EmptyState>
        <EmptyState v-else-if="data" :icon="FileSearchIcon" title="Nothing indexed here" :detail="`${collection} holds no file you can read.`" class="mx-6" />
        <div v-if="cursor !== null" class="flex justify-center px-6 pt-4">
          <Button variant="outline" size="sm" :disabled="loadingMore" @click="loadMore">{{ loadingMore ? 'Loading…' : 'Load more' }}</Button>
        </div>
        <p class="sr-only" aria-live="polite">{{ announced }}</p>
      </CardContent>
    </Card>

    <Card v-if="writeAll">
      <CardHeader>
        <CardTitle>Path tester</CardTitle>
        <CardDescription>Why a file is, or is not, in the index: the rule that skips it, its tags, and how it gets chunked.</CardDescription>
      </CardHeader>
      <CardContent class="space-y-4">
        <form class="flex flex-col gap-2 sm:flex-row" @submit.prevent="runCheck">
          <Input
            v-model="testPath"
            placeholder="acme/web/src/app.ts or /data/codebase/..."
            class="font-mono text-xs"
            autocomplete="off"
            aria-label="Path to check, relative to the codebase root or absolute on the server"
          />
          <Button type="submit" :disabled="checking || !testPath.trim()" class="shrink-0">{{ checking ? 'Checking…' : 'Check' }}</Button>
        </form>
        <div role="status" aria-live="polite">
          <p v-if="checkError" class="text-sm text-destructive">{{ checkError }}</p>
          <div v-else-if="check" class="space-y-3 text-sm">
            <p class="font-mono text-xs break-all text-muted-foreground">{{ check.path }}</p>
            <div v-if="check.skipped" class="rounded-md border border-warning/50 p-3 text-warning">
              <p class="font-medium">Not indexed: skipped</p>
              <p class="mt-1">{{ skipExplanation(check) }}</p>
            </div>
            <dl class="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-1.5">
              <dt class="text-muted-foreground">Indexed</dt>
              <dd>
                <template v-if="check.indexed">Yes, {{ check.chunks }} {{ check.chunks === 1 ? 'chunk' : 'chunks' }}</template>
                <template v-else-if="check.skipped">No</template>
                <template v-else>No, not yet: the next reindex or push of this file would index it</template>
              </dd>
              <dt class="text-muted-foreground">Collection</dt>
              <dd class="font-mono text-xs">{{ check.collection ?? 'None takes this path' }}</dd>
              <dt class="text-muted-foreground">Tags</dt>
              <dd>
                <div v-if="check.tags.length" class="flex flex-wrap gap-1">
                  <Badge v-for="(t, i) in check.tags" :key="t" :variant="i === 0 ? 'secondary' : 'outline'" class="font-mono text-[11px]">{{ t }}</Badge>
                </div>
                <span v-else class="text-muted-foreground">None</span>
                <p v-if="check.tags.length > 1" class="mt-1 text-xs text-muted-foreground">The first tag is the primary label; the others are extra labels for filtering.</p>
              </dd>
              <dt class="text-muted-foreground">Chunker</dt>
              <dd>{{ chunkerLabel(check.chunker) }}<template v-if="check.language"> · {{ check.language }}</template></dd>
            </dl>
            <Button v-if="check.indexed && check.collection" variant="outline" size="sm" @click="openFile(check.collection, check.path)">
              <FileSearchIcon /> Open the indexed file
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>

    <Sheet v-model:open="sheetOpen">
      <SheetContent side="right" class="w-full sm:max-w-2xl">
        <SheetHeader v-if="opened">
          <SheetTitle class="pr-6 break-all">{{ baseName(opened.file_path) }}</SheetTitle>
          <SheetDescription class="font-mono text-xs break-all">{{ opened.file_path }}</SheetDescription>
        </SheetHeader>
        <div class="space-y-5 overflow-y-auto px-4 pb-6 text-sm">
          <p v-if="detailError" role="alert" class="rounded-md bg-destructive/10 p-3 text-destructive">{{ detailError }}</p>
          <p v-else-if="!detail" role="status" class="text-muted-foreground">Loading…</p>
          <template v-else>
            <div v-if="detail.reindexable || detail.deletable" class="flex flex-wrap gap-2">
              <Button v-if="detail.reindexable" variant="outline" size="sm" :disabled="reindexing" @click="reindexFile">
                <RotateCwIcon :class="reindexing && 'animate-spin'" /> Reindex
              </Button>
              <Button
                v-if="detail.deletable"
                variant="outline"
                size="sm"
                class="text-destructive hover:bg-destructive/10 hover:text-destructive"
                @click="confirmRemove = true"
              >
                <Trash2Icon /> Remove from index
              </Button>
            </div>
            <dl class="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-1.5">
              <dt class="text-muted-foreground">Collection</dt><dd class="font-mono text-xs">{{ detail.collection }}</dd>
              <dt class="text-muted-foreground">Tags</dt>
              <dd>
                <div v-if="detail.tags.length" class="flex flex-wrap gap-1">
                  <Badge v-for="(t, i) in detail.tags" :key="t" :variant="i === 0 ? 'secondary' : 'outline'" class="font-mono text-[11px]">{{ t }}</Badge>
                </div>
                <span v-else class="text-muted-foreground">None</span>
              </dd>
              <dt class="text-muted-foreground">Language</dt><dd>{{ detail.language ?? '—' }}</dd>
              <dt class="text-muted-foreground">Last indexed</dt>
              <dd>{{ detail.last_indexed_at ? `${fmtTime(detail.last_indexed_at)} (${ago(detail.last_indexed_at, now)})` : '—' }}</dd>
              <dt class="text-muted-foreground">File modified</dt><dd>{{ detail.file_mtime ? fmtMtime(detail.file_mtime) : '—' }}</dd>
              <template v-if="detail.source_url">
                <dt class="text-muted-foreground">Source</dt>
                <dd class="min-w-0">
                  <a :href="detail.source_url" target="_blank" rel="noopener" class="inline-flex max-w-full items-center gap-1 underline">
                    <span class="truncate">{{ detail.source_url }}</span><ExternalLinkIcon class="size-3.5 shrink-0" aria-hidden="true" />
                    <span class="sr-only">(opens in a new tab)</span>
                  </a>
                </dd>
              </template>
            </dl>
            <section class="space-y-3">
              <h3 class="font-medium">{{ detail.chunks.length }} {{ detail.chunks.length === 1 ? 'chunk' : 'chunks' }}</h3>
              <ol class="space-y-4">
                <li v-for="c in detail.chunks" :key="c.chunk_index" class="space-y-1.5">
                  <p class="flex flex-wrap items-center gap-1.5 text-xs">
                    <Badge variant="outline" class="tabular-nums">#{{ c.chunk_index }}</Badge>
                    <Badge variant="secondary">{{ c.chunk_type }}</Badge>
                    <span v-if="c.symbol_name || c.section" class="min-w-0 truncate font-mono" :title="c.symbol_name ?? c.section ?? ''">{{ c.symbol_name ?? c.section }}</span>
                  </p>
                  <p v-if="c.preamble" class="text-xs whitespace-pre-wrap text-muted-foreground">{{ c.preamble }}</p>
                  <pre class="max-h-72 overflow-auto rounded-md bg-muted p-3 font-mono text-xs" tabindex="0" :aria-label="`Content of chunk ${c.chunk_index}`">{{ c.content }}</pre>
                </li>
              </ol>
            </section>
          </template>
        </div>
      </SheetContent>
    </Sheet>

    <AlertDialog v-model:open="confirmRemove">
      <AlertDialogContent v-if="detail">
        <AlertDialogHeader>
          <AlertDialogTitle>Remove this file from the index?</AlertDialogTitle>
          <AlertDialogDescription>
            Its {{ detail.chunks.length }} {{ detail.chunks.length === 1 ? 'chunk' : 'chunks' }} stop coming back in search.
            The file itself is not touched: the next push or reindex of it adds it back.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction :class="buttonVariants({ variant: 'destructive' })" @click="removeFile">Remove from index</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>

    <ReindexDialog
      v-model:open="reindexOpen"
      :initial-collection="collection in REINDEX_ROOTS ? collection : undefined"
      :initial-path="reindexPath"
    />
  </div>
</template>
