<script setup lang="ts">
/** Starts a reindex of a server path; its progress shows on the Indexing page. */
import { toast } from 'vue-sonner'
import { REINDEX_ROOTS } from '~/utils/indexFiles'

const props = defineProps<{ initialCollection?: string; initialPath?: string }>()
const emit = defineEmits<{ started: [jobId: string] }>()
const open = defineModel<boolean>('open', { required: true })

const api = useApi()
const route = useRoute()
const collections = Object.keys(REINDEX_ROOTS)
const collection = ref('mnemos_code')
const path = ref('')
const full = ref(true)
const submitting = ref(false)

watch(open, (isOpen) => {
  if (!isOpen) return
  collection.value = props.initialCollection && props.initialCollection in REINDEX_ROOTS ? props.initialCollection : 'mnemos_code'
  path.value = props.initialPath ?? REINDEX_ROOTS[collection.value]!
  full.value = true
})
// Follows the collection while the path is still another collection's default root.
watch(collection, (c, previous) => {
  if (previous && path.value === REINDEX_ROOTS[previous]) path.value = REINDEX_ROOTS[c]!
})

const trimmed = computed(() => path.value.trim())

async function submit() {
  if (!trimmed.value) return
  submitting.value = true
  try {
    const { job_id } = await api.reindex({ collection: collection.value, path: trimmed.value, full: full.value })
    open.value = false
    emit('started', job_id)
    toast.success('Reindex started', {
      description: `${collection.value} · ${trimmed.value}`,
      action: route.path === '/indexing' ? undefined : { label: 'View progress', onClick: () => navigateTo('/indexing') },
    })
  } catch (e) {
    toast.error('Could not start the reindex', { description: (e as Error).message })
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <Dialog v-model:open="open">
    <DialogContent class="sm:max-w-lg">
      <DialogHeader>
        <DialogTitle>Reindex a folder</DialogTitle>
        <DialogDescription>
          Reads the files again from the server's disk and replaces their chunks. Progress shows on the
          <NuxtLink to="/indexing" class="underline" @click="open = false">Indexing</NuxtLink> page.
        </DialogDescription>
      </DialogHeader>
      <form class="space-y-5" @submit.prevent="submit">
        <div class="space-y-1.5">
          <Label for="reindex-collection">Collection</Label>
          <Select v-model="collection">
            <SelectTrigger id="reindex-collection" class="w-full"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem v-for="c in collections" :key="c" :value="c">{{ c }}</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div class="space-y-1.5">
          <Label for="reindex-path">Server path</Label>
          <Input id="reindex-path" v-model="path" class="font-mono text-xs" autocomplete="off" aria-describedby="reindex-path-help" />
          <p id="reindex-path-help" class="text-xs text-muted-foreground">
            The path inside the server container, e.g. <code>/data/codebase/acme/web</code>.
          </p>
        </div>
        <div class="flex items-start justify-between gap-4">
          <div class="space-y-0.5">
            <Label for="reindex-full">Every file under this path</Label>
            <p class="text-xs text-muted-foreground">Off: the path is a single file.</p>
          </div>
          <Switch id="reindex-full" v-model="full" />
        </div>
        <DialogFooter>
          <Button type="button" variant="outline" @click="open = false">Cancel</Button>
          <Button type="submit" :disabled="submitting || !trimmed">{{ submitting ? 'Starting…' : 'Start reindex' }}</Button>
        </DialogFooter>
      </form>
    </DialogContent>
  </Dialog>
</template>
