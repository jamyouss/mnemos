<script setup lang="ts">
import { CopyIcon, KeyRoundIcon, LockIcon, MoreHorizontalIcon, PencilIcon, PlusIcon, RefreshCwIcon, Trash2Icon, TriangleAlertIcon, UsersIcon } from '@lucide/vue'
import { toast } from 'vue-sonner'
import { copyToClipboard } from '~/composables/useCopy'
import type { IssuedKey, TenantChange } from '~/composables/useApi'
import { buttonVariants } from '~/components/ui/button'
import { API_KEY_STORAGE } from '~/utils/auth'
import { ago } from '~/utils/connectors'
import {
  describeScope,
  isAdmin,
  sameScope,
  tenantNameProblem,
  toScope,
  toScopeForm,
  type ScopeForm,
  type Tenant,
  STALE_KEY_DAYS,
  staleKey,
} from '~/utils/tenants'

definePageMeta({ title: 'Tenants' })
useHead({ title: 'Tenants' })

const api = useApi()
const { data: me, refresh: refreshMe } = await useWhoami()
const admin = computed(() => isAdmin(me.value))
const { data, error, refresh } = await useAsyncData('tenants', () => api.tenants(), { immediate: admin.value })
const editable = computed(() => data.value?.writable ?? false)

// Scopes are tags of every tagged collection, not only code.
const { data: tagOptions } = useAsyncData('tenant-tags', async () => {
  const results = await Promise.allSettled(
    ['mnemos_code', 'mnemos_knowledge', 'mnemos_memory'].map((collection) => api.facets(collection, 'tags', 500)),
  )
  const counts = new Map<string, number>()
  for (const r of results) {
    if (r.status === 'fulfilled') for (const c of r.value.counts) counts.set(c.value, (counts.get(c.value) ?? 0) + c.count)
  }
  return [...counts].map(([value, count]) => ({ value, count })).sort((a, b) => b.count - a.count)
}, { lazy: true, default: () => [] })

const READ_HELP = 'Which projects its searches, memories and file lists can see. Skills and docs are open to everyone.'
const WRITE_HELP = 'Which projects it may index into and write memories for. Reindexing, skills and docs need all projects.'
const READ_EMPTY = 'No tag: it sees skills and docs only.'
const WRITE_EMPTY = 'No tag: read-only.'

const fmtDate = (iso: string) => (iso ? new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) : '—')
const isSelf = (t: Tenant) => !!me.value?.auth_enabled && t.name === me.value.name
const now = ref(Date.now())
watch(data, () => { now.value = Date.now() })
const lastUsedText = (t: Tenant) =>
  t.last_used_at ? `Last used ${ago(t.last_used_at, now.value)}${t.last_used_ip ? ` from ${t.last_used_ip}` : ''}` : 'Never used'

// ---- Create & edit ---------------------------------------------------------
interface TenantForm {
  name: string
  admin: boolean
  read: ScopeForm
  write: ScopeForm
}

const formOpen = ref(false)
/** The tenant being edited; null while creating one. */
const editing = ref<Tenant | null>(null)
const form = ref<TenantForm>(blankForm())
const submitting = ref(false)

function blankForm(): TenantForm {
  return { name: '', admin: false, read: { all: false, tags: '' }, write: { all: false, tags: '' } }
}

function openCreate() {
  editing.value = null
  form.value = blankForm()
  formOpen.value = true
}

function openEdit(t: Tenant) {
  editing.value = t
  form.value = { name: t.name, admin: t.admin, read: toScopeForm(t.read), write: toScopeForm(t.write) }
  formOpen.value = true
}

const nameProblem = computed(() =>
  editing.value ? null : tenantNameProblem(form.value.name.trim(), (data.value?.tenants ?? []).map((t) => t.name)),
)

/** Only what changed, so an edit never re-sends a scope someone else just set. */
const change = computed<TenantChange>(() => {
  const t = editing.value
  if (!t) return {}
  const out: TenantChange = {}
  if (form.value.admin !== t.admin) out.admin = form.value.admin
  const read = toScope(form.value.read)
  const write = toScope(form.value.write)
  if (!sameScope(read, t.read)) out.read = read
  if (!sameScope(write, t.write)) out.write = write
  return out
})

const canSubmit = computed(() => (editing.value ? Object.keys(change.value).length > 0 : !nameProblem.value))
const demotingSelf = computed(() => !!editing.value && isSelf(editing.value) && editing.value.admin && !form.value.admin)

async function submit() {
  if (!data.value || !canSubmit.value) return
  submitting.value = true
  try {
    if (editing.value) {
      await api.updateTenant(editing.value.name, change.value, data.value.revision)
      toast.success(`Tenant “${editing.value.name}” updated`, { description: 'Every server instance applies it within seconds.' })
      if (isSelf(editing.value)) await refreshMe()
    } else {
      const issued = await api.createTenant({
        name: form.value.name.trim(),
        admin: form.value.admin,
        read: toScope(form.value.read),
        write: toScope(form.value.write),
        revision: data.value.revision,
      })
      showKey(issued, 'created')
    }
    formOpen.value = false
    await refresh()
  } catch (e) {
    toastSaveError(e, refresh, editing.value ? 'Could not update the tenant' : 'Could not create the tenant')
  } finally {
    submitting.value = false
  }
}

// ---- The key, shown once ----------------------------------------------------
const issued = ref<{ name: string; key: string; how: 'created' | 'rotated' } | null>(null)
const keyOpen = computed({
  get: () => issued.value !== null,
  set: (open: boolean) => {
    if (!open) issued.value = null
  },
})

function showKey(result: IssuedKey, how: 'created' | 'rotated') {
  issued.value = { name: result.tenant.name, key: result.api_key, how }
}

function copyKey() {
  if (issued.value) copyToClipboard(issued.value.key, 'API key')
}

// ---- Rotate & delete --------------------------------------------------------
const confirming = ref<{ kind: 'rotate' | 'delete'; tenant: Tenant } | null>(null)
const confirmOpen = computed({
  get: () => confirming.value !== null,
  set: (open: boolean) => {
    if (!open) confirming.value = null
  },
})

async function confirmAction() {
  const pending = confirming.value
  if (!pending || !data.value) return
  confirming.value = null
  const { kind, tenant } = pending
  try {
    if (kind === 'rotate') {
      const result = await api.rotateTenantKey(tenant.name, data.value.revision)
      // The old key dies with the rotation: keep this dashboard signed in.
      if (isSelf(tenant)) window.localStorage.setItem(API_KEY_STORAGE, result.api_key)
      showKey(result, 'rotated')
    } else {
      await api.deleteTenant(tenant.name, data.value.revision)
      toast.success(`Tenant “${tenant.name}” deleted`, { description: 'Its key stops working on every server instance within seconds.' })
    }
    await refresh()
  } catch (e) {
    toastSaveError(e, refresh, kind === 'rotate' ? 'Could not rotate the key' : 'Could not delete the tenant')
  }
}
</script>

<template>
  <div class="space-y-6">
    <PageHeader
      title="Tenants"
      description="Who can use this server, each with an API key. Read and write scopes limit which projects a key can see and change."
    >
      <template #actions>
        <Button v-if="editable" size="sm" @click="openCreate"><PlusIcon /> Add tenant</Button>
        <Button variant="outline" size="sm" @click="refresh()"><RefreshCwIcon /> Refresh</Button>
      </template>
    </PageHeader>

    <AdminOnly v-if="me && !admin" :me="me" what="Tenant management" />

    <EmptyState v-else-if="error" tone="error" title="Could not load the tenants" :detail="error.message" />

    <template v-else-if="data">
      <ReadOnlyAlert :writable="data.writable" :me="me" />

      <Alert v-if="me && !me.auth_enabled">
        <TriangleAlertIcon />
        <AlertTitle>Authentication is off</AlertTitle>
        <AlertDescription>
          Every caller is an admin until authentication is turned on in the server configuration; these keys and scopes
          only apply then.
        </AlertDescription>
      </Alert>

      <Card v-if="data.tenants.length">
        <CardContent class="px-0">
          <p class="px-6 pb-3 text-xs text-muted-foreground">
            A key unused for {{ STALE_KEY_DAYS }} days or more shows in <span class="font-medium text-warning">amber</span>: a candidate for revocation.
          </p>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead class="pl-6">Name</TableHead>
                <TableHead>Role</TableHead>
                <TableHead>Read</TableHead>
                <TableHead>Write</TableHead>
                <TableHead class="hidden md:table-cell">Created</TableHead>
                <TableHead class="hidden sm:table-cell">Last used</TableHead>
                <TableHead class="w-12 pr-6"><span class="sr-only">Actions</span></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              <TableRow v-for="t in data.tenants" :key="t.name">
                <TableCell class="pl-6 font-mono">
                  {{ t.name }}
                  <Badge v-if="isSelf(t)" variant="outline" class="ml-1 font-sans text-[10px]">You</Badge>
                  <p class="font-sans text-xs sm:hidden" :class="staleKey(t.last_used_at, now) ? 'text-warning' : 'text-muted-foreground'">
                    {{ lastUsedText(t) }}
                  </p>
                </TableCell>
                <TableCell>
                  <Badge :variant="t.admin ? 'default' : 'secondary'">{{ t.root ? 'Root' : t.admin ? 'Admin' : 'Member' }}</Badge>
                </TableCell>
                <TableCell class="max-w-56 truncate" :class="t.read !== '*' && 'font-mono text-xs'" :title="describeScope(t.read)">
                  {{ describeScope(t.read) }}
                </TableCell>
                <TableCell class="max-w-56 truncate" :class="t.write !== '*' && 'font-mono text-xs'" :title="describeScope(t.write)">
                  {{ describeScope(t.write) }}
                </TableCell>
                <TableCell class="hidden whitespace-nowrap text-muted-foreground md:table-cell">{{ fmtDate(t.created_at) }}</TableCell>
                <TableCell class="hidden whitespace-nowrap sm:table-cell">
                  <template v-if="t.last_used_at">
                    <span :class="staleKey(t.last_used_at, now) && 'font-medium text-warning'" :title="new Date(t.last_used_at).toLocaleString()">{{ ago(t.last_used_at, now) }}</span>
                    <span v-if="staleKey(t.last_used_at, now)" class="sr-only"> (unused for {{ STALE_KEY_DAYS }} days or more)</span>
                    <span v-if="t.last_used_ip" class="ml-1 font-mono text-xs text-muted-foreground">{{ t.last_used_ip }}</span>
                  </template>
                  <span v-else class="text-muted-foreground">Never used</span>
                </TableCell>
                <TableCell class="pr-6 text-right">
                  <Tooltip v-if="t.root">
                    <TooltipTrigger as-child>
                      <Button variant="ghost" size="icon" class="size-8 text-muted-foreground" :aria-label="`${t.name} is the root tenant`">
                        <LockIcon />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent class="max-w-64">Root tenant: its key is set in the server configuration and can't be changed here.</TooltipContent>
                  </Tooltip>
                  <DropdownMenu v-else-if="editable">
                    <DropdownMenuTrigger as-child>
                      <Button variant="ghost" size="icon" class="size-8" :aria-label="`Actions for ${t.name}`"><MoreHorizontalIcon /></Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem @select="openEdit(t)"><PencilIcon /> Edit role and scopes</DropdownMenuItem>
                      <DropdownMenuItem @select="confirming = { kind: 'rotate', tenant: t }"><KeyRoundIcon /> Rotate key</DropdownMenuItem>
                      <DropdownMenuSeparator />
                      <DropdownMenuItem variant="destructive" @select="confirming = { kind: 'delete', tenant: t }"><Trash2Icon /> Delete</DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <EmptyState v-else :icon="UsersIcon" title="No tenants yet" detail="Create one to issue an API key.">
        <Button v-if="editable" size="sm" @click="openCreate"><PlusIcon /> Add tenant</Button>
      </EmptyState>
    </template>

    <Dialog v-model:open="formOpen">
      <DialogContent class="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{{ editing ? `Edit “${editing.name}”` : 'New tenant' }}</DialogTitle>
          <DialogDescription>
            {{ editing ? 'Takes effect on every server instance within seconds; the key stays the same.' : 'Its API key is shown once, right after creation.' }}
          </DialogDescription>
        </DialogHeader>

        <form class="space-y-5" @submit.prevent="submit">
          <div v-if="!editing" class="space-y-1.5">
            <Label for="tenant-name">Name</Label>
            <Input
              id="tenant-name"
              v-model="form.name"
              class="font-mono"
              placeholder="ci-acme"
              autocomplete="off"
              :aria-invalid="(form.name !== '' && !!nameProblem) || undefined"
              :aria-describedby="form.name && nameProblem ? 'tenant-name-error' : undefined"
            />
            <p v-if="form.name && nameProblem" id="tenant-name-error" class="text-xs text-destructive">{{ nameProblem }}</p>
          </div>

          <div class="flex items-start justify-between gap-4">
            <div class="space-y-0.5">
              <Label for="tenant-admin">Admin</Label>
              <p class="text-xs text-muted-foreground">Reads and changes configuration, connectors, projects, tenants and the query log.</p>
            </div>
            <Switch id="tenant-admin" v-model="form.admin" />
          </div>
          <p v-if="demotingSelf" class="text-xs text-warning" role="alert">This is the key this dashboard uses: without admin, it loses access to this page.</p>

          <ScopeEditor id="tenant-read" v-model="form.read" label="Read" :description="READ_HELP" :empty-meaning="READ_EMPTY" :options="tagOptions" />
          <ScopeEditor id="tenant-write" v-model="form.write" label="Write" :description="WRITE_HELP" :empty-meaning="WRITE_EMPTY" :options="tagOptions" />

          <DialogFooter>
            <Button type="button" variant="outline" @click="formOpen = false">Cancel</Button>
            <Button type="submit" :disabled="submitting || !canSubmit">
              {{ submitting ? 'Saving…' : editing ? 'Save' : 'Create' }}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>

    <!-- Outside clicks do not close it: the key would be gone for good. -->
    <Dialog v-model:open="keyOpen">
      <DialogContent class="sm:max-w-lg" @interact-outside.prevent>
        <DialogHeader>
          <DialogTitle>API key of “{{ issued?.name }}”</DialogTitle>
          <DialogDescription>
            {{ issued?.how === 'rotated' ? 'The previous key no longer works.' : 'Tenant created.' }}
          </DialogDescription>
        </DialogHeader>
        <Alert variant="destructive">
          <TriangleAlertIcon />
          <AlertTitle>Copy it now</AlertTitle>
          <AlertDescription>
            This key is shown once and cannot be shown again: the server keeps only its hash. If it is lost, rotate it.
          </AlertDescription>
        </Alert>
        <div class="flex gap-2">
          <Input :model-value="issued?.key ?? ''" readonly class="font-mono text-xs" aria-label="API key" @focus="($event.target as HTMLInputElement).select()" />
          <Button variant="outline" size="icon" aria-label="Copy the API key" @click="copyKey"><CopyIcon /></Button>
        </div>
        <details class="text-xs text-muted-foreground">
          <summary class="cursor-pointer">How to use it</summary>
          <p class="mt-1.5">
            Clients send it as <code>Authorization: Bearer &lt;key&gt;</code>. The CLI and the git hooks read it from their
            environment.
          </p>
        </details>
        <DialogFooter>
          <Button @click="keyOpen = false">I have copied it</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <AlertDialog v-model:open="confirmOpen">
      <AlertDialogContent v-if="confirming">
        <AlertDialogHeader>
          <AlertDialogTitle>
            {{ confirming.kind === 'rotate' ? `Rotate the key of “${confirming.tenant.name}”?` : `Delete tenant “${confirming.tenant.name}”?` }}
          </AlertDialogTitle>
          <AlertDialogDescription>
            <template v-if="confirming.kind === 'rotate'">
              A new key is issued and the current one stops working on every server instance within seconds: whatever
              uses it (the sync service, CI, MCP clients, git hooks) needs the new one.
              <template v-if="isSelf(confirming.tenant)"> This dashboard switches to the new key by itself.</template>
            </template>
            <template v-else>
              Its key stops working on every server instance within seconds. What it indexed and the memories it wrote stay.
              <template v-if="isSelf(confirming.tenant)"> This dashboard uses that key: you will be signed out.</template>
            </template>
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            :class="confirming.kind === 'delete' && buttonVariants({ variant: 'destructive' })"
            @click="confirmAction"
          >
            {{ confirming.kind === 'rotate' ? 'Rotate key' : 'Delete tenant' }}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  </div>
</template>
