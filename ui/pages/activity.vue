<script setup lang="ts">
import { HistoryIcon, RefreshCwIcon, ShieldAlertIcon, TriangleAlertIcon } from '@lucide/vue'
import { refDebounced } from '@vueuse/core'
import type { AuditEntry } from '~/composables/useApi'
import { changeLines, changeSummary, DOMAINS, entryTitle } from '~/utils/audit'
import { channelLabel } from '~/utils/channels'
import { ago } from '~/utils/connectors'
import { isAdmin } from '~/utils/tenants'

definePageMeta({ title: 'Activity' })
useHead({ title: 'Activity' })

const api = useApi()
const { data: me } = await useWhoami()
const admin = computed(() => isAdmin(me.value))

const ALL = 'all'
const domain = ref(ALL)
const actor = ref(ALL)
const outcome = ref(ALL)
const target = ref('')
const targetApplied = refDebounced(target, 400)
const filters = computed(() => ({
  domain: domain.value === ALL ? undefined : domain.value,
  actor: actor.value === ALL ? undefined : actor.value,
  outcome: outcome.value === ALL ? undefined : outcome.value,
  target: targetApplied.value.trim() || undefined,
}))
const filtered = computed(() => Object.values(filters.value).some((v) => v !== undefined))
function clearFilters() {
  domain.value = ALL
  actor.value = ALL
  outcome.value = ALL
  target.value = ''
}

const { data, error, refresh, pending } = await useAsyncData(
  'audit',
  () => api.audit({ ...filters.value, limit: 50 }),
  { watch: [filters], immediate: admin.value },
)

// Older pages appended under the first one; reset whenever the first reloads.
const older = ref<AuditEntry[]>([])
const cursor = ref<number | null>(null)
watch(data, (page) => {
  older.value = []
  cursor.value = page?.next_before ?? null
}, { immediate: true })
const entries = computed(() => [...(data.value?.items ?? []), ...older.value])
const loadingMore = ref(false)
async function loadMore() {
  if (cursor.value === null) return
  loadingMore.value = true
  try {
    const page = await api.audit({ ...filters.value, before: cursor.value, limit: 50 })
    older.value.push(...page.items)
    cursor.value = page.next_before
  } finally {
    loadingMore.value = false
  }
}
const announced = computed(() => {
  if (!data.value) return ''
  const n = entries.value.length
  return `${n} ${n === 1 ? 'entry' : 'entries'} loaded${cursor.value !== null ? ', older ones available' : ''}`
})

const selected = ref<AuditEntry | null>(null)
const detailOpen = computed({ get: () => selected.value !== null, set: (open) => { if (!open) selected.value = null } })
function filterTarget(value: string) {
  target.value = value
  selected.value = null
}

const now = ref(Date.now())
watch(data, () => { now.value = Date.now() })
const fmtTime = (iso: string) => new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' })
const rowDetail = (e: AuditEntry) => (e.outcome === 'denied' ? e.detail ?? '' : changeSummary(e.changes))
</script>

<template>
  <div class="space-y-6">
    <PageHeader title="Activity">
      <template #description>
        Who changed what: tenants, projects, settings, connectors, the index and memories, and every such change that
        was refused.<template v-if="data"> Kept {{ data.retention_days }} days.</template>
      </template>
      <template v-if="admin" #actions>
        <Input v-model="target" type="search" placeholder="Target…" class="h-8 w-44 font-mono text-xs" aria-label="Target (exact match)" />
        <Select v-model="domain">
          <SelectTrigger size="sm" class="w-40" aria-label="Area"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem :value="ALL">Every area</SelectItem>
            <SelectItem v-for="d in DOMAINS" :key="d.value" :value="d.value">{{ d.label }}</SelectItem>
          </SelectContent>
        </Select>
        <Select v-model="actor">
          <SelectTrigger size="sm" class="w-40" aria-label="Who"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem :value="ALL">Everyone</SelectItem>
            <SelectItem v-for="a in data?.actors ?? []" :key="a" :value="a">{{ a }}</SelectItem>
          </SelectContent>
        </Select>
        <Select v-model="outcome">
          <SelectTrigger size="sm" class="w-36" aria-label="Outcome"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem :value="ALL">Any outcome</SelectItem>
            <SelectItem value="ok">Done</SelectItem>
            <SelectItem value="denied">Refused</SelectItem>
          </SelectContent>
        </Select>
        <Button variant="outline" size="sm" :disabled="pending" @click="refresh()">
          <RefreshCwIcon :class="pending && 'animate-spin'" /> Refresh
        </Button>
      </template>
    </PageHeader>

    <AdminOnly v-if="me && !admin" :me="me" what="The activity log" />

    <EmptyState v-else-if="error" tone="error" title="Could not read the activity log" :detail="error.message" />

    <template v-else-if="data">
      <Alert v-if="data.write_failures > 0" variant="destructive">
        <TriangleAlertIcon />
        <AlertTitle>Some changes are missing from this log</AlertTitle>
        <AlertDescription>
          {{ data.write_failures }} audit {{ data.write_failures === 1 ? 'entry' : 'entries' }} could not be stored on this
          server since it started<template v-if="data.last_write_failure_at"> (last {{ ago(data.last_write_failure_at, now) }})</template>.
          They are still in the server log (logger <code>mnemos.audit</code>).
        </AlertDescription>
      </Alert>

      <EmptyState
        v-if="!entries.length && filtered"
        :icon="HistoryIcon"
        title="No matching entries"
        detail="No entry matches these filters."
      >
        <Button variant="outline" size="sm" @click="clearFilters">Clear filters</Button>
      </EmptyState>

      <EmptyState
        v-else-if="!entries.length"
        :icon="HistoryIcon"
        title="Nothing recorded"
        detail="Changes to tenants, projects, settings, connectors, the index and memories show up here."
      />

      <Card v-else>
        <CardContent class="px-0">
          <div class="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead class="hidden pl-6 sm:table-cell">When</TableHead>
                  <TableHead class="hidden sm:table-cell">Who</TableHead>
                  <TableHead class="pl-6 sm:pl-2">What</TableHead>
                  <TableHead class="hidden pr-6 sm:table-cell">Changes</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                <TableRow
                  v-for="e in entries"
                  :key="e.id"
                  class="cursor-pointer"
                  :class="e.outcome === 'denied' && 'bg-destructive/5'"
                  @click="selected = e"
                >
                  <TableCell class="hidden pl-6 whitespace-nowrap text-muted-foreground sm:table-cell">{{ fmtTime(e.at) }}</TableCell>
                  <TableCell class="hidden whitespace-nowrap sm:table-cell">
                    <span class="font-medium">{{ e.actor }}</span>
                    <Badge variant="outline" class="ml-2 text-[11px]">{{ channelLabel(e.via) }}</Badge>
                  </TableCell>
                  <TableCell class="max-w-[calc(100vw-4rem)] pl-6 whitespace-normal sm:max-w-md sm:min-w-56 sm:pl-2">
                    <button
                      type="button"
                      class="-mx-1 flex items-center gap-1.5 rounded-md px-1 text-left outline-none hover:underline focus-visible:ring-3 focus-visible:ring-ring/50"
                      @click.stop="selected = e"
                    >
                      <ShieldAlertIcon v-if="e.outcome === 'denied'" class="size-3.5 shrink-0 text-destructive" aria-hidden="true" />
                      <span :class="e.outcome === 'denied' && 'text-destructive'">{{ entryTitle(e) }}</span>
                    </button>
                    <p v-if="e.target" class="truncate font-mono text-xs text-muted-foreground" :title="e.target">{{ e.target }}</p>
                    <p class="text-xs text-muted-foreground sm:hidden">{{ fmtTime(e.at) }} · {{ e.actor }} · {{ channelLabel(e.via) }}</p>
                  </TableCell>
                  <TableCell class="hidden max-w-sm pr-6 sm:table-cell">
                    <p class="truncate text-xs text-muted-foreground" :title="rowDetail(e)">{{ rowDetail(e) || '—' }}</p>
                  </TableCell>
                </TableRow>
              </TableBody>
            </Table>
          </div>
          <div v-if="cursor !== null" class="flex justify-center px-6 pt-4">
            <Button variant="outline" size="sm" :disabled="loadingMore" @click="loadMore">{{ loadingMore ? 'Loading…' : 'Load older entries' }}</Button>
          </div>
        </CardContent>
      </Card>
    </template>

    <p class="sr-only" aria-live="polite">{{ announced }}</p>

    <Sheet v-model:open="detailOpen">
      <SheetContent side="right" class="w-full sm:max-w-lg">
        <SheetHeader v-if="selected">
          <SheetTitle>{{ entryTitle(selected) }}</SheetTitle>
          <SheetDescription>{{ fmtTime(selected.at) }} by {{ selected.actor }}</SheetDescription>
        </SheetHeader>
        <div v-if="selected" class="space-y-5 overflow-y-auto px-4 pb-6 text-sm">
          <dl class="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-1.5">
            <dt class="text-muted-foreground">Target</dt>
            <dd class="font-mono break-all">
              <button
                v-if="selected.target"
                type="button"
                class="rounded-sm text-left underline decoration-dotted underline-offset-2 outline-none hover:decoration-solid focus-visible:ring-3 focus-visible:ring-ring/50"
                :aria-label="`Show only entries for ${selected.target}`"
                @click="filterTarget(selected.target)"
              >
                {{ selected.target }}
              </button>
              <template v-else>—</template>
            </dd>
            <dt class="text-muted-foreground">Action</dt><dd class="font-mono">{{ selected.action }}</dd>
            <dt class="text-muted-foreground">Through</dt><dd>{{ channelLabel(selected.via) }}</dd>
            <dt class="text-muted-foreground">Address</dt><dd class="font-mono">{{ selected.ip || '—' }}</dd>
          </dl>
          <div v-if="selected.detail" class="rounded-md bg-destructive/10 p-3 text-destructive">{{ selected.detail }}</div>
          <div v-if="changeLines(selected.changes).length" class="space-y-1.5">
            <h3 class="font-medium">Changes</h3>
            <ul class="space-y-1 font-mono text-xs break-all">
              <li v-for="line in changeLines(selected.changes)" :key="line">{{ line }}</li>
            </ul>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  </div>
</template>
