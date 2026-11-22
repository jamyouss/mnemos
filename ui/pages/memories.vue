<script setup lang="ts">
import { toast } from 'vue-sonner'

definePageMeta({ title: 'Memories' })
useHead({ title: 'Memories' })

const api = useApi()
const { data, error, refresh } = await useAsyncData('memories', () => api.memories())

const ALL = 'all'
const search = ref('')
const type = ref(ALL)
const status = ref('approved')
const busy = ref<string | null>(null)

const all = computed(() => data.value?.entries ?? [])
const types = computed(() => [...new Set(all.value.map((m) => m.memory_type).filter(Boolean))].sort())
const counts = computed(() => {
  const out: Record<string, number> = { approved: 0, rejected: 0, pending: 0 }
  for (const m of all.value) out[m.status] = (out[m.status] ?? 0) + 1
  return out
})

const rows = computed(() =>
  all.value
    .filter((m) => status.value === ALL || m.status === status.value)
    .filter((m) => type.value === ALL || m.memory_type === type.value)
    .filter((m) => {
      const q = search.value.trim().toLowerCase()
      if (!q) return true
      return m.content.toLowerCase().includes(q) || (m.tags ?? []).some((t) => t.toLowerCase().includes(q))
    })
    .sort((a, b) => (b.created_at ?? '').localeCompare(a.created_at ?? '')),
)

async function reject(id: string) {
  busy.value = id
  try {
    await api.reviewMemory(id, 'reject')
    toast.success('Memory rejected', { description: 'It no longer comes back in search.' })
    await refresh()
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

    <EmptyState v-if="error" title="Could not load memories" detail="GET /api/memory failed." />

    <template v-else>
      <div class="flex flex-wrap items-center gap-2">
        <Tabs v-model="status">
          <TabsList>
            <TabsTrigger value="approved">Approved <Badge variant="secondary" class="ml-1">{{ counts.approved }}</Badge></TabsTrigger>
            <TabsTrigger value="rejected">Rejected <Badge variant="secondary" class="ml-1">{{ counts.rejected }}</Badge></TabsTrigger>
            <TabsTrigger value="pending">Pending <Badge variant="secondary" class="ml-1">{{ counts.pending }}</Badge></TabsTrigger>
            <TabsTrigger :value="ALL">All</TabsTrigger>
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
        v-if="!rows.length"
        title="Nothing matches"
        detail="Memories come from your commits, the MCP tool and `mnemos memory add`."
      />

      <div v-else class="grid gap-3">
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
    </template>
  </div>
</template>
