<script setup lang="ts">
import { ArrowRightIcon, RefreshCwIcon, SearchIcon, TriangleAlertIcon } from '@lucide/vue'
import { toast } from 'vue-sonner'
import { ApiError, type ConfigField, type ConfigResponse } from '~/composables/useApi'
import { APPLY_LABEL } from '~/components/ConfigFieldRow.vue'
import { isAdmin } from '~/utils/tenants'

definePageMeta({ title: 'Configuration' })
useHead({ title: 'Configuration' })

const api = useApi()
const { data: me } = await useWhoami()
const admin = computed(() => isAdmin(me.value))
const { data, error, refresh } = await useAsyncData('config', () => api.config(), { immediate: admin.value })

const draft = ref<Record<string, unknown>>({})
const errors = ref<Record<string, string>>({})
const query = ref('')
const tab = ref<string>('')
const saving = ref(false)
const confirmOpen = ref(false)

function load(cfg: ConfigResponse | null | undefined) {
  if (!cfg) return
  const values: Record<string, unknown> = {}
  for (const s of cfg.sections) for (const f of s.fields) values[f.key] = f.value
  draft.value = values
  errors.value = {}
  if (!tab.value || !cfg.sections.some((s) => s.id === tab.value)) tab.value = cfg.sections[0]?.id ?? ''
}
watch(data, load, { immediate: true })

const fields = computed(() => {
  const out: Record<string, ConfigField> = {}
  for (const s of data.value?.sections ?? []) for (const f of s.fields) out[f.key] = f
  return out
})

const changes = computed(() =>
  Object.values(fields.value)
    .filter((f) => f.editable && draft.value[f.key] !== f.value)
    .map((f) => ({ field: f, from: f.value, to: draft.value[f.key] })),
)
const dirtyKeys = computed(() => new Set(changes.value.map((c) => c.field.key)))
const pendingRestart = computed(() => new Set(data.value?.pending_restart ?? []))

const visibleSections = computed(() => {
  const q = query.value.trim().toLowerCase()
  return (data.value?.sections ?? []).map((s) => ({
    ...s,
    fields: s.fields.filter(
      (f) => !q || [f.label, f.key, f.env, f.description].some((t) => t.toLowerCase().includes(q)),
    ),
  }))
})
// While searching, show every section that has a hit instead of the tabs.
const searching = computed(() => query.value.trim().length > 0)

function discard() {
  load(data.value)
}

async function save() {
  if (!data.value) return
  saving.value = true
  errors.value = {}
  try {
    const values = Object.fromEntries(changes.value.map((c) => [c.field.key, c.to]))
    const res = await api.updateConfig(values, data.value.revision)
    // Assigning `data` reloads the draft, so read what was sent from `values`.
    const restart = res.pending_restart.filter((k) => k in values)
    data.value = res
    confirmOpen.value = false
    const n = Object.keys(values).length
    toast.success(`${n} setting${n > 1 ? 's' : ''} saved`, {
      description: restart.length
        ? `Applied now: ${res.applied?.length ?? 0}. After a server restart: ${labels(restart)}.`
        : 'Applied to the running servers.',
    })
  } catch (e) {
    confirmOpen.value = false
    if (e instanceof ApiError && e.body?.errors) {
      errors.value = e.body.errors
      const first = Object.keys(e.body.errors)[0]
      const section = data.value?.sections.find((s) => s.fields.some((f) => f.key === first))
      if (section) tab.value = section.id
    }
    toastSaveError(e, refresh)
  } finally {
    saving.value = false
  }
}

async function reset(field: ConfigField) {
  if (!data.value) return
  try {
    data.value = await api.resetConfig(field.key, data.value.revision)
    const back = fields.value[field.key]?.source === 'env' ? 'environment' : 'default'
    toast.success(`${field.label} reset`, { description: `Back to the ${back} value.` })
  } catch (e) {
    toastSaveError(e, refresh, `Could not reset ${field.label}`)
  }
}

const fmt = (v: unknown) => (v === '' || v === null || v === undefined ? '—' : String(v))
const labels = (keys: string[]) => keys.map((k) => fields.value[k]?.label ?? k).join(', ')

onBeforeRouteLeave(() => {
  if (changes.value.length && !window.confirm('Discard unsaved configuration changes?')) return false
})
</script>

<template>
  <div class="space-y-6 pb-20">
    <PageHeader title="Configuration">
      <template #description>
        Settings used by the running servers. A value saved here wins over the environment and applies to every
        instance within seconds.
      </template>
      <template #actions>
        <div class="relative">
          <SearchIcon class="absolute top-2.5 left-2.5 size-4 text-muted-foreground" />
          <Input v-model="query" placeholder="Find a setting…" aria-label="Find a setting" class="h-9 w-56 pl-8" />
        </div>
        <Button variant="outline" size="sm" @click="refresh()"><RefreshCwIcon /> Refresh</Button>
      </template>
    </PageHeader>

    <AdminOnly v-if="me && !admin" :me="me" what="The configuration" />

    <EmptyState v-else-if="error" tone="error" title="Could not load the configuration" :detail="error.message" />

    <template v-else-if="data">
      <ReadOnlyAlert :writable="data.writable" :me="me" />

      <Alert v-if="data.pending_restart.length" class="border-warning/50">
        <TriangleAlertIcon class="text-warning" />
        <AlertTitle>Restart required</AlertTitle>
        <AlertDescription>
          Saved, but not active until the server restarts: {{ labels(data.pending_restart) }}.
        </AlertDescription>
      </Alert>

      <Tabs v-if="!searching" v-model="tab">
        <TabsList class="h-auto flex-wrap">
          <TabsTrigger v-for="s in data.sections" :key="s.id" :value="s.id">
            {{ s.label }}
            <Badge v-if="s.fields.some((f) => dirtyKeys.has(f.key))" class="ml-1 size-2 rounded-full p-0">
              <span class="sr-only">(unsaved changes)</span>
            </Badge>
          </TabsTrigger>
        </TabsList>
      </Tabs>

      <template v-for="s in visibleSections" :key="s.id">
        <Card v-if="searching ? s.fields.length : s.id === tab">
          <CardHeader>
            <CardTitle>{{ s.label }}</CardTitle>
          </CardHeader>
          <CardContent class="divide-y">
            <ConfigFieldRow
              v-for="f in s.fields"
              :key="f.key"
              v-model="draft[f.key]"
              :field="f"
              :writable="data.writable"
              :dirty="dirtyKeys.has(f.key)"
              :error="errors[f.key]"
              :pending-restart="pendingRestart.has(f.key)"
              @reset="reset(f)"
            />
          </CardContent>
        </Card>
      </template>
      <EmptyState v-if="searching && !visibleSections.some((s) => s.fields.length)" title="No setting matches" :detail="`Nothing named or described like “${query}”.`" />
    </template>

    <!-- Sticky save bar: only when there is something to save. -->
    <Transition
      enter-active-class="transition duration-200 ease-out"
      enter-from-class="translate-y-4 opacity-0"
      leave-active-class="transition duration-150 ease-in"
      leave-to-class="translate-y-4 opacity-0"
    >
      <div v-if="changes.length" class="fixed inset-x-0 bottom-6 z-20 flex justify-center px-4">
        <div class="flex items-center gap-3 rounded-xl border bg-popover px-4 py-2.5 shadow-lg">
          <span class="flex items-center gap-2 text-sm" role="status" aria-live="polite">
            Unsaved changes <Badge variant="secondary">{{ changes.length }}</Badge>
          </span>
          <Button variant="ghost" size="sm" @click="discard">Discard</Button>
          <Button size="sm" @click="confirmOpen = true">Review and save</Button>
        </div>
      </div>
    </Transition>

    <Dialog v-model:open="confirmOpen">
      <DialogContent class="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Apply {{ changes.length }} change{{ changes.length > 1 ? 's' : '' }}?</DialogTitle>
          <DialogDescription>Saved on the server and shared by every instance. Live settings apply within seconds; the others after a restart.</DialogDescription>
        </DialogHeader>
        <ul class="divide-y rounded-lg border text-sm">
          <li v-for="c in changes" :key="c.field.key" class="flex items-center gap-3 px-3 py-2">
            <div class="min-w-0 flex-1">
              <p class="truncate font-medium" :title="c.field.label">{{ c.field.label }}</p>
              <p class="truncate font-mono text-[11px] text-muted-foreground" :title="c.field.env">{{ c.field.env }}</p>
            </div>
            <code class="max-w-40 truncate rounded bg-muted px-1.5 py-0.5 text-xs" :title="fmt(c.from)">{{ fmt(c.from) }}</code>
            <ArrowRightIcon class="size-3.5 shrink-0 text-muted-foreground" />
            <code class="max-w-40 truncate rounded bg-primary/10 px-1.5 py-0.5 text-xs" :title="fmt(c.to)">{{ fmt(c.to) }}</code>
            <Badge variant="outline" class="text-[10px]" :class="c.field.apply !== 'live' && 'border-warning text-warning'">{{ APPLY_LABEL[c.field.apply] }}</Badge>
          </li>
        </ul>
        <DialogFooter>
          <Button variant="outline" @click="confirmOpen = false">Cancel</Button>
          <Button :disabled="saving" @click="save">{{ saving ? 'Saving…' : 'Apply' }}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  </div>
</template>
