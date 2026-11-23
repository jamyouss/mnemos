<script setup lang="ts">
import { CopyIcon, HistoryIcon, Loader2Icon, PlayIcon } from '@lucide/vue'
import { toast } from 'vue-sonner'
import { ApiError, type SearchResponse, type TraceCandidate } from '~/composables/useApi'
import { copyToClipboard } from '~/composables/useCopy'
import { API_KEY_STORAGE } from '~/utils/auth'
import { buildBody, type PlaygroundForm, TARGETS, toCurl, toMcpCall, validationMessage } from '~/utils/playground'

definePageMeta({ title: 'Playground' })
useHead({ title: 'Playground' })

const api = useApi()
const HISTORY_KEY = 'mnemos.playground.history'

const form = reactive<PlaygroundForm>({
  target: 'search-code',
  query: '',
  limit: 10,
  mode: 'preview',
  trace: true,
  bypassCache: true,
  collections: [],
  tagsAny: '',
  tagsAll: '',
  fileTypes: '',
  pathFilter: '',
  language: '',
  symbolType: '',
  memoryType: '',
})

// Suggestions for the filter fields, from what is actually indexed.
const status = useAsyncData('status', () => api.status(), { lazy: true })
const tagFacets = useAsyncData('pg-tags', () => api.facets('mnemos_code', 'tags', 50), { lazy: true })
const langFacets = useAsyncData('pg-langs', () => api.facets('mnemos_code', 'language', 30), { lazy: true })
const typeFacets = useAsyncData('pg-chunk-types', () => api.facets('mnemos_code', 'chunk_type', 30), { lazy: true })
const memoryStats = useAsyncData('memory-stats', () => api.memoryStats(), { lazy: true })

const facetOptions = (facets: { counts: Array<{ value: string; count: number }> } | null | undefined) => facets?.counts ?? []
const memoryTypes = computed(() =>
  Object.entries(memoryStats.data.value?.by_type ?? {}).map(([value, count]) => ({ value, count: Number(count) })),
)

const collections = computed(() =>
  Object.keys(status.data.value?.collections ?? {})
    .filter((n) => n !== 'mnemos_memory' && !n.endsWith('_cache') && !n.includes('_eval'))
    .sort(),
)

const running = ref(false)
const response = ref<SearchResponse | null>(null)
const ran = ref<{ form: PlaygroundForm; body: Record<string, unknown>; ms: number } | null>(null)
const view = ref('results')
const history = ref<PlaygroundForm[]>(JSON.parse(localStorage.getItem(HISTORY_KEY) ?? '[]'))

async function run() {
  if (!form.query.trim()) return
  const body = buildBody(form)
  running.value = true
  const started = performance.now()
  try {
    response.value = await api.runSearch(form.target, body)
    ran.value = { form: { ...form, collections: [...form.collections] }, body, ms: performance.now() - started }
    history.value = [ran.value.form, ...history.value.filter((h) => h.query !== form.query || h.target !== form.target)].slice(0, 10)
    localStorage.setItem(HISTORY_KEY, JSON.stringify(history.value))
  } catch (e) {
    const detail = e instanceof ApiError ? (validationMessage(e.body?.detail) ?? e.message) : (e as Error).message
    toast.error('Search failed', { description: detail })
  } finally {
    running.value = false
  }
}

function load(entry: PlaygroundForm) {
  Object.assign(form, entry, { collections: [...entry.collections] })
}

const curl = computed(() =>
  ran.value ? toCurl(window.location.origin, ran.value.form.target, ran.value.body, !!localStorage.getItem(API_KEY_STORAGE)) : '',
)
const mcp = computed(() => (ran.value ? toMcpCall(ran.value.form.target, ran.value.body) : ''))

/** The trace row of the result at `index`, matched on its final rank. */
const traceOf = (index: number): TraceCandidate | undefined =>
  response.value?.trace?.candidates.find((c) => c.final_rank === index + 1)

const title = (r: Record<string, any>) => r.skill_name || r.file_path
const subtitle = (r: Record<string, any>) =>
  [r.collection?.replace(/^mnemos_/, ''), r.language, r.chunk_type, r.symbol_name, r.memory_type].filter(Boolean).join(' · ')
const tagsOf = (r: Record<string, any>): string[] => r.tags ?? r.metadata?.tags ?? []
const text = (r: Record<string, any>) => r.content ?? r.instructions_preview ?? r.description ?? ''
const rank = (n: number | null | undefined) => (n == null ? '—' : `#${n}`)
</script>

<template>
  <div class="space-y-6">
    <PageHeader
      title="Playground"
      description="Run a search the way an agent would, and see why each result came back. Searches run here are left out of the dashboard figures unless you include them."
    />

    <Card>
      <CardContent class="space-y-4">
        <form class="flex flex-wrap gap-2" @submit.prevent="run">
          <Input v-model="form.query" placeholder="Ask what an agent would ask…" class="h-10 min-w-0 flex-1 sm:min-w-72" aria-label="Query" autofocus />
          <Select v-model="form.target">
            <SelectTrigger class="h-10 w-48" aria-label="Search in"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem v-for="t in TARGETS" :key="t.id" :value="t.id">{{ t.label }}</SelectItem>
            </SelectContent>
          </Select>
          <Button type="submit" class="h-10" :disabled="running || !form.query.trim()">
            <Loader2Icon v-if="running" class="animate-spin" />
            <PlayIcon v-else />
            Search
          </Button>
        </form>

        <div class="flex flex-wrap items-center gap-x-6 gap-y-3 text-sm">
          <label class="flex items-center gap-2">
            Limit
            <Input v-model.number="form.limit" type="number" min="1" max="50" class="h-8 w-20" />
          </label>
          <ToggleGroup v-if="form.target !== 'search-skills'" v-model="form.mode" type="single" variant="outline" size="sm" aria-label="Result content">
            <ToggleGroupItem value="preview">Preview</ToggleGroupItem>
            <ToggleGroupItem value="full">Full chunks</ToggleGroupItem>
          </ToggleGroup>
          <label class="flex items-center gap-2"><Switch v-model="form.trace" /> Trace</label>
          <label v-if="form.target === 'search'" class="flex items-center gap-2"><Switch v-model="form.bypassCache" /> Bypass cache</label>
        </div>

        <div v-if="form.target !== 'search-skills'" class="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <template v-if="form.target === 'search'">
            <div class="space-y-1.5 sm:col-span-2 lg:col-span-4">
              <Label id="pg-coll-label">Collections <span class="font-normal text-muted-foreground">(none = all but memories)</span></Label>
              <ToggleGroup v-model="form.collections" type="multiple" variant="outline" size="sm" class="flex-wrap justify-start" aria-labelledby="pg-coll-label">
                <ToggleGroupItem v-for="c in collections" :key="c" :value="c" class="font-mono text-xs">{{ c.replace(/^mnemos_/, '') }}</ToggleGroupItem>
              </ToggleGroup>
            </div>
            <div class="space-y-1.5">
              <Label for="pg-types">File types</Label>
              <FacetCombobox id="pg-types" v-model="form.fileTypes" :options="facetOptions(langFacets.data.value)" multiple />
            </div>
          </template>
          <div class="space-y-1.5">
            <Label for="pg-any">Tags, any of</Label>
            <FacetCombobox id="pg-any" v-model="form.tagsAny" :options="facetOptions(tagFacets.data.value)" multiple />
          </div>
          <div class="space-y-1.5">
            <Label for="pg-all">Tags, all of</Label>
            <FacetCombobox id="pg-all" v-model="form.tagsAll" :options="facetOptions(tagFacets.data.value)" multiple />
          </div>
          <template v-if="form.target === 'search-code'">
            <div class="space-y-1.5">
              <Label for="pg-lang">Language</Label>
              <FacetCombobox id="pg-lang" v-model="form.language" :options="facetOptions(langFacets.data.value)" />
            </div>
            <div class="space-y-1.5">
              <Label for="pg-sym">Symbol type</Label>
              <FacetCombobox id="pg-sym" v-model="form.symbolType" :options="facetOptions(typeFacets.data.value)" />
            </div>
          </template>
          <div v-if="form.target === 'search' || form.target === 'search-code'" class="space-y-1.5">
            <Label for="pg-path">Exact file path</Label><Input id="pg-path" v-model="form.pathFilter" />
          </div>
          <div v-if="form.target === 'search-memory'" class="space-y-1.5">
            <Label for="pg-mtype">Memory type</Label>
            <FacetCombobox id="pg-mtype" v-model="form.memoryType" :options="memoryTypes" />
          </div>
        </div>

        <div v-if="history.length" role="group" aria-label="Recent searches" class="flex flex-wrap items-center gap-1.5 text-xs">
          <HistoryIcon class="size-3.5 text-muted-foreground" aria-hidden="true" />
          <Button
            v-for="(h, i) in history"
            :key="i"
            variant="ghost"
            size="sm"
            class="h-6 max-w-64 px-2 text-xs"
            :title="`${h.target}: ${h.query}`"
            @click="load(h)"
          >
            <span class="truncate">{{ h.query }}</span>
          </Button>
        </div>
      </CardContent>
    </Card>

    <!-- Mounted before any result: a live region inserted with its text is not announced. -->
    <p class="sr-only" aria-live="polite">{{ running ? '' : response ? `${response.results.length} ${response.results.length === 1 ? 'result' : 'results'}` : '' }}</p>

    <EmptyState v-if="!response" title="No search yet" detail="Results and the trace of each stage show up here." />

    <template v-else>
      <div class="flex flex-wrap items-center justify-between gap-3">
        <Tabs v-model="view">
          <TabsList>
            <TabsTrigger value="results">Results <Badge variant="secondary" class="ml-1">{{ response.results.length }}</Badge></TabsTrigger>
            <TabsTrigger value="trace" :disabled="!response.trace">Trace</TabsTrigger>
          </TabsList>
        </Tabs>
        <div class="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
          <span class="tabular-nums">{{ Math.round(ran!.ms) }} ms round trip</span>
          <Button variant="outline" size="sm" @click="copyToClipboard(curl, 'curl command')"><CopyIcon /> Copy as curl</Button>
          <Button variant="outline" size="sm" @click="copyToClipboard(mcp, 'MCP call')"><CopyIcon /> Copy as MCP call</Button>
        </div>
      </div>

      <TracePanel v-if="view === 'trace' && response.trace" :trace="response.trace" />

      <template v-else>
        <EmptyState
          v-if="!response.results.length"
          title="Nothing came back"
          detail="Loosen a filter, or open the trace: it shows whether any candidate was retrieved at all."
        />
        <!-- grid-cols-1 is minmax(0, 1fr): an implicit track would widen to the longest path. -->
        <div v-else class="grid grid-cols-1 gap-3">
          <Card v-for="(r, i) in response.results" :key="i" class="gap-2 py-4">
            <CardContent class="space-y-2 px-4">
              <div class="flex items-start gap-3">
                <Badge class="tabular-nums">#{{ i + 1 }}</Badge>
                <div class="min-w-0 flex-1">
                  <p class="truncate font-mono text-sm" :title="title(r)">{{ title(r) }}</p>
                  <p class="truncate text-xs text-muted-foreground" :title="subtitle(r)">{{ subtitle(r) }}</p>
                </div>
                <span class="text-sm font-medium tabular-nums"><span class="sr-only">Score </span>{{ Number(r.score).toFixed(3) }}</span>
              </div>
              <div v-if="tagsOf(r).length" class="flex flex-wrap gap-1">
                <Badge v-for="t in tagsOf(r)" :key="t" variant="outline" class="font-mono text-[11px]">{{ t }}</Badge>
              </div>
              <p v-if="traceOf(i)" class="text-xs text-muted-foreground tabular-nums">
                dense {{ rank(traceOf(i)!.dense_rank) }} · BM25 {{ rank(traceOf(i)!.sparse_rank) }} · fused {{ rank(traceOf(i)!.fused_rank) }}
                <template v-if="traceOf(i)!.rerank_rank != null"> · rerank {{ rank(traceOf(i)!.rerank_rank) }}</template>
                <template v-if="traceOf(i)!.grade"> · grade {{ traceOf(i)!.grade }}</template>
              </p>
              <pre class="max-h-80 overflow-auto rounded-md bg-muted/60 p-3 font-mono text-xs whitespace-pre-wrap [overflow-wrap:anywhere]">{{ text(r) }}</pre>
            </CardContent>
          </Card>
        </div>
      </template>
    </template>
  </div>
</template>
