<script setup lang="ts">
import { refDebounced } from '@vueuse/core'
import { toast } from 'vue-sonner'

definePageMeta({ title: 'Memories' })
useHead({ title: 'Memories' })

const api = useApi()
const ALL = 'all'

const status = ref('approved')
const type = ref(ALL)
const search = ref('')
const query = refDebounced(search, 300)
const page = ref(1)
const pageSize = ref<number>(50)
const busy = ref<string | null>(null)

// Filtering, sorting and slicing happen on the server, over every memory:
// a page here is a window on all matches, never on what happened to load.
const list = await useAsyncData(
  'memories',
  () =>
    api.memories({
      status: status.value === ALL ? undefined : status.value,
      type: type.value === ALL ? undefined : type.value,
      q: query.value.trim() || undefined,
      offset: (page.value - 1) * pageSize.value,
      limit: pageSize.value,
    }),
  { watch: [status, type, query, page, pageSize] },
)
// Tab counts and the type list come from the stats, which count everything.
const stats = await useAsyncData('memory-stats', () => api.memoryStats())

// A new filter starts over at page 1; otherwise page 7 of the old list
// would silently become page 7 of a list with 2 pages.
watch([status, type, query, pageSize], () => (page.value = 1))

const rows = computed(() => list.data.value?.entries ?? [])
const total = computed(() => list.data.value?.total ?? 0)
const counts = computed(() => stats.data.value?.by_status ?? {})
const types = computed(() => Object.keys(stats.data.value?.by_type ?? {}).sort())

async function reject(id: string) {
  busy.value = id
  try {
    await api.reviewMemory(id, 'reject')
    toast.success('Memory rejected', { description: 'It no longer comes back in search.' })
    await Promise.all([list.refresh(), stats.refresh()])
    // Rejecting the last row of the last page empties it: step back.
    const pages = Math.max(1, Math.ceil(total.value / pageSize.value))
    if (page.value > pages) page.value = pages
  } catch (e) {
    toast.error('Could not reject the memory', { description: (e as Error).message })
  } finally {
    busy.value = null
  }
}
</script>

<template>
  <div class="space-y-6">
    <PageHeader title="Memories">
      <template #description>
        Memories are written searchable. This screen is for taking a wrong one back out, not for letting good ones in —
        search returns <code class="text-xs">approved</code> entries only.
      </template>
    </PageHeader>

    <EmptyState v-if="list.error.value" title="Could not load memories" :detail="list.error.value.message" />

    <template v-else>
      <div class="flex flex-wrap items-center gap-2">
        <Tabs v-model="status">
          <TabsList>
            <TabsTrigger value="approved">Approved <Badge variant="secondary" class="ml-1">{{ counts.approved ?? 0 }}</Badge></TabsTrigger>
            <TabsTrigger value="rejected">Rejected <Badge variant="secondary" class="ml-1">{{ counts.rejected ?? 0 }}</Badge></TabsTrigger>
            <TabsTrigger value="pending">Pending <Badge variant="secondary" class="ml-1">{{ counts.pending ?? 0 }}</Badge></TabsTrigger>
            <TabsTrigger :value="ALL">All <Badge variant="secondary" class="ml-1">{{ stats.data.value?.total ?? 0 }}</Badge></TabsTrigger>
          </TabsList>
        </Tabs>
        <Input v-model="search" placeholder="Filter by content or tag…" class="h-9 min-w-56 flex-1" />
        <Select v-model="type">
          <SelectTrigger class="w-40"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem :value="ALL">Every type</SelectItem>
            <SelectItem v-for="t in types" :key="t" :value="t">{{ t }}</SelectItem>
          </SelectContent>
        </Select>
      </div>

      <EmptyState
        v-if="!rows.length && !list.pending.value"
        title="Nothing matches"
        detail="Memories come from your commits, the MCP tool and `mnemos memory add`."
      />

      <div v-else class="grid gap-3" :class="list.pending.value && 'opacity-60 transition-opacity'">
        <Card v-for="m in rows" :key="m.id" class="gap-3 py-4">
          <CardContent class="flex items-start justify-between gap-4 px-4">
            <div class="min-w-0 flex-1 space-y-2">
              <div class="flex flex-wrap items-center gap-1.5">
                <Badge>{{ m.memory_type }}</Badge>
                <Badge v-for="t in m.tags" :key="t" variant="outline" class="font-mono">{{ t }}</Badge>
                <Badge v-if="m.status !== 'approved'" variant="secondary" class="text-warning">{{ m.status }}</Badge>
              </div>
              <p class="whitespace-pre-wrap text-sm">{{ m.content }}</p>
              <p class="font-mono text-[11px] text-muted-foreground">
                {{ m.id }}<span v-if="m.created_at"> · {{ new Date(m.created_at).toLocaleDateString() }}</span>
              </p>
            </div>
            <Button
              v-if="m.status !== 'rejected'"
              variant="outline"
              size="sm"
              class="shrink-0 text-destructive hover:bg-destructive/10 hover:text-destructive"
              :disabled="busy === m.id"
              @click="reject(m.id)"
            >
              {{ busy === m.id ? 'Rejecting…' : 'Reject' }}
            </Button>
          </CardContent>
        </Card>
      </div>

      <ListPager v-model:page="page" v-model:page-size="pageSize" :total="total" />
    </template>
  </div>
</template>
