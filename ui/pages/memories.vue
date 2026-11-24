<script setup lang="ts">
import { PencilIcon, PlusIcon, Trash2Icon } from '@lucide/vue'
import { refDebounced } from '@vueuse/core'
import { toast } from 'vue-sonner'
import type { MemoryEntry } from '~/composables/useApi'
import { buttonVariants } from '~/components/ui/button'
import { memoryChange, splitTags, toMemoryForm, typeOptions, type MemoryForm } from '~/utils/memories'
import { canWriteSome } from '~/utils/tenants'

definePageMeta({ title: 'Memories' })
useHead({ title: 'Memories' })

const api = useApi()
const { data: me } = await useWhoami()
const writable = computed(() => canWriteSome(me.value))
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
// "No memories yet" only when there are none at all; a status tab counts as a filter.
const none = computed(() => (stats.data.value?.total ?? 0) === 0)
const shortId = (id: string) => id.slice(0, 8)
const sentence = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)

async function reload() {
  await Promise.all([list.refresh(), stats.refresh()])
  // Taking the last row off the last page empties it: step back.
  const pages = Math.max(1, Math.ceil(total.value / pageSize.value))
  if (page.value > pages) page.value = pages
}

async function review(id: string, action: 'approve' | 'reject') {
  busy.value = id
  try {
    await api.reviewMemory(id, action)
    if (action === 'reject') toast.success('Memory rejected', { description: 'It no longer comes back in search.' })
    else toast.success('Memory approved', { description: 'It comes back in search again.' })
    await reload()
  } catch (e) {
    toast.error(`Could not ${action} the memory`, { description: (e as Error).message })
  } finally {
    busy.value = null
  }
}

// ---- Create & edit -------------------------------------------------------------
const formOpen = ref(false)
/** The memory being edited; null while creating one. */
const editing = ref<MemoryEntry | null>(null)
const form = ref<MemoryForm>(blankForm())
const submitting = ref(false)

function blankForm(): MemoryForm {
  return { content: '', memory_type: 'note', project: '', tags: '' }
}

function openCreate() {
  editing.value = null
  form.value = blankForm()
  formOpen.value = true
}

function openEdit(m: MemoryEntry) {
  editing.value = m
  form.value = toMemoryForm(m)
  formOpen.value = true
}

const change = computed(() => (editing.value ? memoryChange(editing.value, form.value) : {}))
const canSubmit = computed(() =>
  form.value.content.trim() !== '' && (editing.value ? Object.keys(change.value).length > 0 : true),
)

async function submit() {
  if (!canSubmit.value) return
  submitting.value = true
  try {
    if (editing.value) {
      await api.updateMemory(editing.value.id, change.value)
      toast.success('Memory updated')
    } else {
      const project = form.value.project.trim()
      const created = await api.createMemory({
        content: form.value.content.trim(),
        memory_type: form.value.memory_type,
        project: project || undefined,
        tags: splitTags(form.value.tags),
        status: 'approved',
      })
      if (created.action === 'merged') {
        toast.success('Merged into an existing memory', { description: `It was folded into memory ${shortId(created.id)}, a near-duplicate.` })
      } else if (created.action === 'replaced') {
        toast.success('Memory stored', { description: `It replaces the near-identical memory ${shortId(created.id)}.` })
      } else {
        toast.success('Memory stored', { description: 'It is searchable right away.' })
      }
    }
    formOpen.value = false
    await reload()
  } catch (e) {
    toast.error(editing.value ? 'Could not update the memory' : 'Could not store the memory', { description: (e as Error).message })
  } finally {
    submitting.value = false
  }
}

// ---- Delete ------------------------------------------------------------------------
const deleting = ref<MemoryEntry | null>(null)
const deleteOpen = computed({ get: () => deleting.value !== null, set: (open) => { if (!open) deleting.value = null } })

async function confirmDelete() {
  const m = deleting.value
  if (!m) return
  deleting.value = null
  busy.value = m.id
  try {
    await api.deleteMemory(m.id)
    toast.success('Memory deleted')
    await reload()
  } catch (e) {
    toast.error('Could not delete the memory', { description: (e as Error).message })
  } finally {
    busy.value = null
  }
}
</script>

<template>
  <div class="space-y-6">
    <PageHeader
      title="Memories"
      description="New memories are searchable right away. Reject one here to take it out of search results."
    >
      <template v-if="writable" #actions>
        <Button size="sm" @click="openCreate"><PlusIcon /> New memory</Button>
      </template>
    </PageHeader>

    <EmptyState v-if="list.error.value" tone="error" title="Could not load memories" :detail="list.error.value.message" />

    <template v-else>
      <div class="flex flex-wrap items-center gap-2">
        <Tabs v-model="status" class="max-w-full overflow-x-auto">
          <TabsList>
            <TabsTrigger value="approved">Approved <Badge variant="secondary" class="ml-1">{{ counts.approved ?? 0 }}</Badge></TabsTrigger>
            <TabsTrigger value="rejected">Rejected <Badge variant="secondary" class="ml-1">{{ counts.rejected ?? 0 }}</Badge></TabsTrigger>
            <TabsTrigger value="pending">Pending <Badge variant="secondary" class="ml-1">{{ counts.pending ?? 0 }}</Badge></TabsTrigger>
            <TabsTrigger :value="ALL">All <Badge variant="secondary" class="ml-1">{{ stats.data.value?.total ?? 0 }}</Badge></TabsTrigger>
          </TabsList>
        </Tabs>
        <Input v-model="search" type="search" placeholder="Search content or tags…" class="h-9 min-w-56 flex-1" aria-label="Search memories by content or tag" />
        <Select v-model="type">
          <SelectTrigger class="w-40" aria-label="Memory type"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem :value="ALL">Every type</SelectItem>
            <SelectItem v-for="t in types" :key="t" :value="t">{{ sentence(t) }}</SelectItem>
          </SelectContent>
        </Select>
      </div>

      <p class="sr-only" aria-live="polite">{{ list.pending.value ? '' : `${total} ${total === 1 ? 'memory' : 'memories'}` }}</p>

      <EmptyState
        v-if="!rows.length && !list.pending.value"
        :title="none ? 'No memories yet' : 'Nothing matches these filters'"
        :detail="none ? 'Memories are extracted from commits or added by agents.' : undefined"
      >
        <Button v-if="none && writable" size="sm" @click="openCreate"><PlusIcon /> New memory</Button>
      </EmptyState>

      <div v-else class="grid grid-cols-1 gap-3" :class="list.pending.value && 'opacity-60 transition-opacity'" :aria-busy="list.pending.value">
        <Card v-for="m in rows" :key="m.id" class="gap-3 py-4">
          <CardContent class="flex flex-col gap-3 px-4 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
            <div class="min-w-0 flex-1 space-y-2">
              <div class="flex flex-wrap items-center gap-1.5">
                <Badge>{{ sentence(m.memory_type) }}</Badge>
                <Badge v-for="t in m.tags" :key="t" variant="outline" class="font-mono">{{ t }}</Badge>
                <Badge v-if="m.status !== 'approved'" variant="outline">{{ sentence(m.status) }}</Badge>
              </div>
              <p class="text-sm break-words whitespace-pre-wrap">{{ m.content }}</p>
              <p class="font-mono text-[11px] text-muted-foreground">
                <span :title="m.id">{{ shortId(m.id) }}</span><span v-if="m.project"> · {{ m.project }}</span><span v-if="m.created_at"> · {{ new Date(m.created_at).toLocaleDateString() }}</span>
              </p>
            </div>
            <div v-if="writable" class="flex shrink-0 flex-wrap items-center gap-2">
              <Button
                v-if="m.status !== 'approved'"
                variant="outline"
                size="sm"
                :disabled="busy === m.id"
                :aria-label="`Approve memory ${shortId(m.id)}`"
                @click="review(m.id, 'approve')"
              >
                Approve
              </Button>
              <Button
                v-if="m.status !== 'rejected'"
                variant="outline"
                size="sm"
                class="text-destructive hover:bg-destructive/10 hover:text-destructive"
                :disabled="busy === m.id"
                :aria-label="`Reject memory ${shortId(m.id)}`"
                @click="review(m.id, 'reject')"
              >
                Reject
              </Button>
              <Button variant="ghost" size="icon" class="size-8" :disabled="busy === m.id" :aria-label="`Edit memory ${shortId(m.id)}`" @click="openEdit(m)">
                <PencilIcon />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                class="size-8 text-destructive hover:bg-destructive/10 hover:text-destructive"
                :disabled="busy === m.id"
                :aria-label="`Delete memory ${shortId(m.id)}`"
                @click="deleting = m"
              >
                <Trash2Icon />
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>

      <ListPager v-model:page="page" v-model:page-size="pageSize" :total="total" />
    </template>

    <Dialog v-model:open="formOpen">
      <DialogContent class="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{{ editing ? `Edit memory ${shortId(editing.id)}` : 'New memory' }}</DialogTitle>
          <DialogDescription>
            {{ editing ? 'Only the fields you change are saved.' : 'Stored as approved and searchable right away. A near-duplicate of an existing memory is merged into it.' }}
          </DialogDescription>
        </DialogHeader>
        <form class="space-y-4" @submit.prevent="submit">
          <div class="space-y-1.5">
            <Label for="memory-content">Content</Label>
            <textarea
              id="memory-content"
              v-model="form.content"
              rows="6"
              required
              class="w-full min-w-0 rounded-md border border-input bg-transparent px-3 py-2 text-base shadow-xs outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 md:text-sm dark:bg-input/30"
              placeholder="We chose RRF over a weighted sum because…"
            />
          </div>
          <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div class="space-y-1.5">
              <Label for="memory-type">Type</Label>
              <Select v-model="form.memory_type">
                <SelectTrigger id="memory-type" class="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem v-for="t in typeOptions(form.memory_type)" :key="t" :value="t">{{ sentence(t) }}</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div class="space-y-1.5">
              <Label for="memory-project">Project</Label>
              <Input id="memory-project" v-model="form.project" placeholder="Optional" autocomplete="off" />
            </div>
          </div>
          <div class="space-y-1.5">
            <Label for="memory-tags">Tags</Label>
            <Input id="memory-tags" v-model="form.tags" class="font-mono text-xs" placeholder="acme, search" autocomplete="off" aria-describedby="memory-tags-help" />
            <p id="memory-tags-help" class="text-xs text-muted-foreground">Separated by commas or spaces. They decide which keys can read it.</p>
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" @click="formOpen = false">Cancel</Button>
            <Button type="submit" :disabled="submitting || !canSubmit">{{ submitting ? 'Saving…' : editing ? 'Save' : 'Store memory' }}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>

    <AlertDialog v-model:open="deleteOpen">
      <AlertDialogContent v-if="deleting">
        <AlertDialogHeader>
          <AlertDialogTitle>Delete memory {{ shortId(deleting.id) }}?</AlertDialogTitle>
          <AlertDialogDescription>
            It is removed for good, not just hidden from search: rejecting it keeps it reviewable, deleting does not.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction :class="buttonVariants({ variant: 'destructive' })" @click="confirmDelete">Delete memory</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  </div>
</template>
