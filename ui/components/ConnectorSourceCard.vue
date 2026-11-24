<script setup lang="ts">
import { BookOpenIcon, ChevronDownIcon, GitBranchIcon, GlobeIcon, KeyRoundIcon, RefreshCcwDotIcon, TicketIcon, Trash2Icon } from '@lucide/vue'
import type { ConnectorRun, ConnectorSyncRequest } from '~/composables/useApi'
import { DEPLOYMENTS, GIT_COLLECTIONS, ago, runSummary, scopeTags, toPayload, waitingLabel, type Deployment, type GitCollection, type SourceForm } from '~/utils/connectors'

const source = defineModel<SourceForm>({ required: true })
const props = defineProps<{
  editable: boolean
  /** Chunks per tag in the knowledge and code collections; undefined while loading, null when unavailable. */
  indexed?: Record<string, number> | null
  /** In the saved configuration: only those can be synced, and they start collapsed. */
  saved: boolean
  /** The oldest sync request covering this source that waits for the sync service. */
  waiting?: ConnectorSyncRequest | null
  /** The sync service is silent or never reported: a waiting request may wait for good. */
  serviceDown: boolean
  /** Unsaved changes: a sync runs the saved configuration. The page says why. */
  syncBlocked: boolean
  /** What the sync service last reported for this source. */
  run?: ConnectorRun | null
  /** Clock for "synced 4 min ago", advanced by the page. */
  now: number
}>()
const emit = defineEmits<{ remove: []; sync: [] }>()

const open = ref(!props.saved)

const meta = {
  confluence: { label: 'Confluence', icon: BookOpenIcon, keys: 'Spaces', keysHint: 'Space keys, e.g. ENG, OPS', url: 'https://wiki.example.com or https://<site>.atlassian.net', token: 'CONFLUENCE_TOKEN' },
  jira: { label: 'Jira', icon: TicketIcon, keys: 'Projects', keysHint: 'Project keys, e.g. OPS', url: 'https://jira.example.com or https://<site>.atlassian.net', token: 'JIRA_TOKEN' },
  web: { label: 'Web site', icon: GlobeIcon, keys: '', keysHint: '', url: '', token: '' },
  git: { label: 'Git repository', icon: GitBranchIcon, keys: '', keysHint: '', url: 'https://github.com/acme/app.git', token: 'GITHUB_TOKEN' },
} as const
const collectionLabel: Record<GitCollection, string> = { mnemos_code: 'Code', mnemos_knowledge: 'Knowledge' }
const deploymentLabel: Record<Deployment, string> = { auto: 'Auto', cloud: 'Cloud', datacenter: 'Data Center' }
// Function declarations, not a template index: vue-tsc 3.3 types `obj[loopVar]` in a template as a ref access.
function collectionName(c: GitCollection) {
  return collectionLabel[c]
}
function deploymentName(d: Deployment) {
  return deploymentLabel[d]
}

const m = computed(() => meta[source.value.type])
const id = (field: string) => `${source.value.id}-${field}`
const scopes = computed(() => scopeTags(toPayload(source.value)))
// One line for the collapsed header; the footer keeps the per-tag detail.
const summary = computed(() => {
  if (!scopes.value.length || props.indexed === null) return ''
  if (props.indexed === undefined) return 'counting…'
  const total = scopes.value.reduce((sum, tag) => sum + (props.indexed?.[tag] ?? 0), 0)
  return total ? `${total.toLocaleString()} chunks` : 'not synced yet'
})
const lastRun = computed(() => runSummary(props.run, props.now))
const running = computed(() => props.run?.state === 'running')
const status = computed(() => {
  if (running.value) return { text: 'Syncing', badge: 'secondary' as const, class: '', textClass: 'text-foreground' }
  if (props.waiting) {
    const warn = props.serviceDown
    return {
      text: waitingLabel(props.waiting, props.now),
      badge: warn ? 'outline' as const : 'secondary' as const,
      class: warn ? 'border-warning text-warning' : '',
      textClass: warn ? 'text-warning' : 'text-foreground',
    }
  }
  if (props.run?.state === 'failed') return { text: 'Sync failed', badge: 'destructive' as const, class: '', textClass: 'text-destructive' }
  return null
})
</script>

<template>
  <Card :class="!open && 'py-4'">
    <CardHeader class="flex flex-row items-center gap-3 space-y-0">
      <button
        type="button"
        class="-m-1 flex min-w-0 flex-1 items-center gap-3 rounded-lg p-1 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring"
        :aria-expanded="open"
        :aria-controls="id('body')"
        @click="open = !open"
      >
        <div class="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted">
          <component :is="m.icon" class="size-4" />
        </div>
        <div class="min-w-0 flex-1">
          <CardTitle class="truncate text-base">{{ source.name.trim() || m.label }}</CardTitle>
          <CardDescription class="line-clamp-2">
            {{ m.label }}<template v-if="summary"> · {{ summary }}</template><template v-if="lastRun"> · {{ lastRun }}</template>
          </CardDescription>
          <span v-if="status" class="mt-1 flex items-center gap-1.5 text-xs sm:hidden" :class="status.textClass">
            <span class="size-1.5 shrink-0 rounded-full bg-current" aria-hidden="true" />{{ status.text }}
          </span>
        </div>
        <ChevronDownIcon class="size-4 shrink-0 text-muted-foreground transition-transform" :class="open && 'rotate-180'" />
      </button>
      <Badge
        v-if="status"
        :variant="status.badge"
        class="hidden max-w-64 sm:inline-flex"
        :class="status.class"
        :title="status.text"
        role="status"
      >
        <span class="truncate" :title="status.text">{{ status.text }}</span>
      </Badge>
      <Button
        v-if="props.saved"
        variant="outline"
        size="sm"
        :disabled="props.syncBlocked || !!props.waiting || running"
        :aria-label="`Sync ${source.name.trim() || m.label} now`"
        @click="emit('sync')"
      >
        <RefreshCcwDotIcon :class="running && 'animate-spin'" /><span class="hidden sm:inline">Sync</span>
      </Button>
      <Button
        v-if="props.editable"
        variant="ghost"
        size="icon"
        class="size-8 text-muted-foreground hover:text-destructive"
        :aria-label="`Remove ${source.name.trim() || m.label}`"
        @click="emit('remove')"
      >
        <Trash2Icon />
      </Button>
    </CardHeader>

    <div v-show="open" :id="id('body')" class="flex flex-col gap-6">
    <CardContent class="grid gap-4 md:grid-cols-2">
      <div class="space-y-1.5">
        <Label :for="id('name')">Name</Label>
        <Input :id="id('name')" v-model="source.name" :placeholder="m.label" :disabled="!editable" />
        <p class="text-xs text-muted-foreground">Unique name for this source.</p>
      </div>

      <template v-if="source.type === 'web'">
        <div class="space-y-1.5">
          <Label :for="id('max')">Max pages per host</Label>
          <Input
            :id="id('max')"
            type="number"
            min="1"
            :model-value="source.max_pages ?? undefined"
            :disabled="!editable"
            @update:model-value="(v) => (source.max_pages = v === '' ? null : Number(v))"
          />
          <p class="text-xs text-muted-foreground">A truncated crawl pushes what it saw and deletes nothing.</p>
        </div>
        <div class="space-y-1.5 md:col-span-2">
          <Label :for="id('urls')">Seed URLs</Label>
          <Input :id="id('urls')" v-model="source.urls" class="font-mono text-xs" placeholder="https://docs.example.com/handbook/" :disabled="!editable" />
          <p class="text-xs text-muted-foreground">Comma- or space-separated. The crawl stays on each host, under each URL's path.</p>
        </div>
      </template>

      <template v-else-if="source.type === 'git'">
        <div class="space-y-1.5">
          <Label :for="id('collection')">Collection</Label>
          <Select v-model="source.collection" :disabled="!editable">
            <SelectTrigger :id="id('collection')" class="w-full"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem v-for="c in GIT_COLLECTIONS" :key="c" :value="c">{{ collectionName(c) }}</SelectItem>
            </SelectContent>
          </Select>
          <p class="text-xs text-muted-foreground">Code: found by code search. Knowledge: searched with Confluence and Jira.</p>
        </div>
        <div class="space-y-1.5 md:col-span-2">
          <Label :for="id('url')">Repository URL</Label>
          <Input :id="id('url')" v-model="source.url" class="font-mono text-xs" :placeholder="m.url" :disabled="!editable" />
          <p class="text-xs text-muted-foreground">HTTPS, on any host: GitHub, GitLab, Gitea, self-hosted.</p>
        </div>
        <div class="space-y-1.5">
          <Label :for="id('token')" class="flex items-center gap-1.5">
            <KeyRoundIcon class="size-3.5" /> Token variable <span class="font-normal text-muted-foreground">(optional)</span>
          </Label>
          <Input :id="id('token')" v-model="source.token_env" class="font-mono text-xs" :placeholder="m.token" :disabled="!editable" />
          <p class="text-xs text-muted-foreground">
            Variable holding a read-only token in the sync service's environment. Blank for a public repository.
          </p>
        </div>
        <div class="space-y-1.5">
          <Label :for="id('user')">User <span class="font-normal text-muted-foreground">(optional)</span></Label>
          <Input :id="id('user')" v-model="source.user" placeholder="oauth2" :disabled="!editable" />
          <p class="text-xs text-muted-foreground">Sent with the token. <code>oauth2</code> suits GitLab and GitHub.</p>
        </div>
        <div class="space-y-1.5">
          <Label :for="id('branch')">Branch <span class="font-normal text-muted-foreground">(optional)</span></Label>
          <Input :id="id('branch')" v-model="source.branch" class="font-mono text-xs" placeholder="default branch" :disabled="!editable" />
        </div>
        <div class="space-y-1.5">
          <Label :for="id('maxkb')">Max file size (KB)</Label>
          <Input
            :id="id('maxkb')"
            type="number"
            min="1"
            :model-value="source.max_file_kb ?? undefined"
            :disabled="!editable"
            @update:model-value="(v) => (source.max_file_kb = v === '' ? null : Number(v))"
          />
          <p class="text-xs text-muted-foreground">Larger files, and binary ones, are skipped.</p>
        </div>
        <div class="space-y-1.5">
          <Label :for="id('paths')">Paths <span class="font-normal text-muted-foreground">(optional)</span></Label>
          <Input :id="id('paths')" v-model="source.paths" class="font-mono text-xs" placeholder="docs/, services/api" :disabled="!editable" />
          <p class="text-xs text-muted-foreground">Only files under these repository paths. Blank: the whole repository.</p>
        </div>
        <div class="space-y-1.5">
          <Label :for="id('exclude')">Exclude <span class="font-normal text-muted-foreground">(optional)</span></Label>
          <Input :id="id('exclude')" v-model="source.exclude" class="font-mono text-xs" placeholder="*.lock, */testdata/*" :disabled="!editable" />
          <p class="text-xs text-muted-foreground">Globs on the repository path; <code>*</code> crosses <code>/</code>. The server's own filter applies too.</p>
        </div>
      </template>

      <template v-else>
        <div class="space-y-1.5">
          <Label :for="id('keys')">{{ m.keys }}</Label>
          <Input :id="id('keys')" v-model="source.keys" :placeholder="m.keysHint" :disabled="!editable" />
          <p class="text-xs text-muted-foreground">Comma- or space-separated. Case does not matter.</p>
        </div>
        <div class="space-y-1.5 md:col-span-2">
          <Label :for="id('url')">Site URL</Label>
          <Input :id="id('url')" v-model="source.url" class="font-mono text-xs" :placeholder="m.url" :disabled="!editable" />
        </div>
        <div class="space-y-1.5">
          <Label :for="id('token')" class="flex items-center gap-1.5"><KeyRoundIcon class="size-3.5" /> Token variable</Label>
          <Input :id="id('token')" v-model="source.token_env" class="font-mono text-xs" :placeholder="m.token" :disabled="!editable" />
          <p class="text-xs text-muted-foreground">
            Name of the variable holding the token in the sync service's environment. The token itself never goes here.
          </p>
        </div>
        <div class="space-y-1.5">
          <Label :for="id('user')">User <span class="font-normal text-muted-foreground">(optional)</span></Label>
          <Input :id="id('user')" v-model="source.user" placeholder="bot@example.com" :disabled="!editable" />
          <p class="text-xs text-muted-foreground">Atlassian Cloud: your account email. Leave blank for a personal access token (Data Center).</p>
        </div>
        <div class="space-y-1.5">
          <Label :for="id('deployment')">Deployment</Label>
          <Select v-model="source.deployment" :disabled="!editable">
            <SelectTrigger :id="id('deployment')" class="w-full"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem v-for="d in DEPLOYMENTS" :key="d" :value="d">{{ deploymentName(d) }}</SelectItem>
            </SelectContent>
          </Select>
          <p class="text-xs text-muted-foreground">Auto: Cloud for *.atlassian.net sites, Data Center otherwise.</p>
        </div>
        <div v-if="source.type === 'jira'" class="space-y-1.5">
          <Label :for="id('jql')">Extra JQL <span class="font-normal text-muted-foreground">(optional)</span></Label>
          <Input :id="id('jql')" v-model="source.jql" class="font-mono text-xs" placeholder="resolution = Unresolved OR updated >= -180d" :disabled="!editable" />
          <p class="text-xs text-muted-foreground">Added to the project filter with AND. Do not include ORDER BY.</p>
        </div>
      </template>

      <div class="space-y-1.5">
        <Label :for="id('tags')">Tags <span class="font-normal text-muted-foreground">(optional)</span></Label>
        <Input :id="id('tags')" v-model="source.tags" placeholder="acme, handbook" :disabled="!editable" />
        <p class="text-xs text-muted-foreground">Added to every document, next to <code>{{ source.type }}:&lt;scope&gt;</code>.</p>
      </div>
    </CardContent>

    <div v-if="run?.results.length" class="space-y-2 border-t px-6 pt-4">
      <p class="text-xs text-muted-foreground">
        Last sync<template v-if="run.finished_at">, {{ ago(run.finished_at, now) }}</template>:
      </p>
      <div v-for="r in run.results" :key="r.scope_tag" class="space-y-1">
        <p class="text-xs">
          <span class="font-mono break-all">{{ r.scope_tag }}</span>
          <span class="text-muted-foreground">
            — {{ r.pushed }} pushed · {{ r.unchanged }} unchanged · {{ r.deleted }} deleted<template v-if="r.skipped"> · {{ r.skipped }} skipped</template>
          </span>
          <span v-if="r.failed" class="text-destructive"> · {{ r.failed }} failed</span>
        </p>
        <pre v-if="r.error" class="whitespace-pre-wrap break-words rounded-md bg-destructive/10 p-2 font-mono text-[11px] text-destructive">{{ r.error }}</pre>
      </div>
    </div>

    <CardFooter v-if="scopes.length" class="flex flex-wrap gap-2 border-t pt-4">
      <span class="text-xs text-muted-foreground">Index status:</span>
      <Badge v-for="tag in scopes" :key="tag" variant="outline" class="max-w-full justify-start rounded-md font-mono text-[11px] break-all whitespace-normal">
        {{ tag }}
        <span class="ml-1" :class="indexed?.[tag] ? 'text-foreground' : 'text-muted-foreground'">
          {{ indexed === undefined ? 'counting…' : indexed === null ? 'unknown' : indexed[tag] ? `${indexed[tag]} chunks` : 'not synced yet' }}
        </span>
      </Badge>
    </CardFooter>
    </div>
  </Card>
</template>
