<script setup lang="ts">
import { HistoryIcon, RefreshCwIcon, ShieldAlertIcon } from '@lucide/vue'
import type { AuditEntry } from '~/composables/useApi'
import { actionLabel, changeLines, changeSummary, DOMAINS } from '~/utils/audit'
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
const filters = computed(() => ({
  domain: domain.value === ALL ? undefined : domain.value,
  actor: actor.value === ALL ? undefined : actor.value,
  outcome: outcome.value === ALL ? undefined : outcome.value,
}))

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

const selected = ref<AuditEntry | null>(null)
const detailOpen = computed({ get: () => selected.value !== null, set: (open) => { if (!open) selected.value = null } })

const fmtTime = (iso: string) => new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' })
</script>

<template>
  <div class="space-y-6">
    <PageHeader title="Activity">
      <template #description>
        Who changed what: tenants, projects, settings, connectors, the index and memories, and every such change that
        was refused.<template v-if="data"> Kept {{ data.retention_days }} days.</template>
      </template>
      <template v-if="admin" #actions>
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

    <EmptyState
      v-else-if="data && !entries.length"
      :icon="HistoryIcon"
      title="Nothing recorded"
      :detail="domain === ALL && actor === ALL && outcome === ALL
        ? 'Changes to tenants, projects, settings, connectors, the index and memories show up here.'
        : 'No entry matches these filters.'"
    />

    <Card v-else-if="data">
      <CardContent class="px-0">
        <div class="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead class="pl-6">When</TableHead>
                <TableHead>Who</TableHead>
                <TableHead>What</TableHead>
                <TableHead class="pr-6">Changes</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              <TableRow
                v-for="e in entries"
                :key="e.id"
                class="cursor-pointer"
                :class="e.outcome === 'denied' && 'bg-destructive/5'"
                tabindex="0"
                @click="selected = e"
                @keydown.enter="selected = e"
              >
                <TableCell class="pl-6 whitespace-nowrap text-muted-foreground">{{ fmtTime(e.at) }}</TableCell>
                <TableCell class="whitespace-nowrap">
                  <span class="font-medium">{{ e.actor }}</span>
                  <Badge variant="outline" class="ml-2 text-[11px]">{{ e.via }}</Badge>
                </TableCell>
                <TableCell class="max-w-md min-w-56">
                  <p class="flex items-center gap-1.5">
                    <ShieldAlertIcon v-if="e.outcome === 'denied'" class="size-3.5 shrink-0 text-destructive" aria-hidden="true" />
                    <span v-if="e.outcome === 'denied'" class="text-destructive">Refused:</span>
                    {{ actionLabel(e.action) }}
                  </p>
                  <p v-if="e.target" class="truncate font-mono text-xs text-muted-foreground" :title="e.target">{{ e.target }}</p>
                </TableCell>
                <TableCell class="max-w-sm pr-6">
                  <p class="truncate text-xs text-muted-foreground" :title="changeSummary(e.changes)">
                    {{ e.outcome === 'denied' ? e.detail : changeSummary(e.changes) || '—' }}
                  </p>
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

    <Sheet v-model:open="detailOpen">
      <SheetContent side="right" class="w-full sm:max-w-lg">
        <SheetHeader v-if="selected">
          <SheetTitle>{{ selected.outcome === 'denied' ? 'Refused: ' : '' }}{{ actionLabel(selected.action) }}</SheetTitle>
          <SheetDescription>{{ fmtTime(selected.at) }} by {{ selected.actor }}</SheetDescription>
        </SheetHeader>
        <div v-if="selected" class="space-y-5 overflow-y-auto px-4 pb-6 text-sm">
          <dl class="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-1.5">
            <dt class="text-muted-foreground">Target</dt><dd class="font-mono break-all">{{ selected.target || '—' }}</dd>
            <dt class="text-muted-foreground">Action</dt><dd class="font-mono">{{ selected.action }}</dd>
            <dt class="text-muted-foreground">Through</dt><dd>{{ selected.via }}</dd>
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
