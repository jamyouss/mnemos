<script setup lang="ts">
import { BookOpenIcon, GitBranchIcon, GlobeIcon, KeyRoundIcon, TicketIcon, Trash2Icon } from '@lucide/vue'
import { DEPLOYMENTS, GIT_COLLECTIONS, scopeTags, toPayload, type SourceForm } from '~/utils/connectors'

const source = defineModel<SourceForm>({ required: true })
const props = defineProps<{
  editable: boolean
  /** Chunks per tag in mnemos_knowledge and mnemos_code; null while unknown. */
  indexed: Record<string, number> | null
}>()
const emit = defineEmits<{ remove: [] }>()

const meta = {
  confluence: { label: 'Confluence', icon: BookOpenIcon, keys: 'Spaces', keysHint: 'Space keys, e.g. ENG, OPS', url: 'https://wiki.example.com or https://<site>.atlassian.net', token: 'CONFLUENCE_TOKEN' },
  jira: { label: 'Jira', icon: TicketIcon, keys: 'Projects', keysHint: 'Project keys, e.g. OPS', url: 'https://jira.example.com or https://<site>.atlassian.net', token: 'JIRA_TOKEN' },
  web: { label: 'Web site', icon: GlobeIcon, keys: '', keysHint: '', url: '', token: '' },
  git: { label: 'Git repository', icon: GitBranchIcon, keys: '', keysHint: '', url: 'https://github.com/acme/app.git', token: 'GITHUB_TOKEN' },
} as const

const m = computed(() => meta[source.value.type])
const id = (field: string) => `${source.value.id}-${field}`
const scopes = computed(() => scopeTags(toPayload(source.value)))
</script>

<template>
  <Card>
    <CardHeader class="flex flex-row items-center gap-3 space-y-0">
      <div class="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted">
        <component :is="m.icon" class="size-4" />
      </div>
      <div class="min-w-0 flex-1">
        <CardTitle class="truncate text-base">{{ source.name.trim() || source.type }}</CardTitle>
        <CardDescription>{{ m.label }}</CardDescription>
      </div>
      <Button
        v-if="props.editable"
        variant="ghost"
        size="icon"
        class="size-8 text-muted-foreground hover:text-destructive"
        :aria-label="`Remove ${source.name || source.type}`"
        @click="emit('remove')"
      >
        <Trash2Icon />
      </Button>
    </CardHeader>

    <CardContent class="grid gap-4 md:grid-cols-2">
      <div class="space-y-1.5">
        <Label :for="id('name')">Name</Label>
        <Input :id="id('name')" v-model="source.name" :placeholder="source.type" :disabled="!editable" />
        <p class="text-xs text-muted-foreground">Unique; what <code>mnemos ingest run --only</code> selects.</p>
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
              <SelectItem v-for="c in GIT_COLLECTIONS" :key="c" :value="c">{{ c }}</SelectItem>
            </SelectContent>
          </Select>
          <p class="text-xs text-muted-foreground"><code>mnemos_code</code>: found by code search. <code>mnemos_knowledge</code>: next to Confluence and Jira.</p>
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
            Variable holding a read-only token in the ingester's environment (<code>.env</code>). Blank for a public repository.
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
          <p class="text-xs text-muted-foreground">Comma- or space-separated; upper-cased like Atlassian shows them.</p>
        </div>
        <div class="space-y-1.5 md:col-span-2">
          <Label :for="id('url')">Site URL</Label>
          <Input :id="id('url')" v-model="source.url" class="font-mono text-xs" :placeholder="m.url" :disabled="!editable" />
        </div>
        <div class="space-y-1.5">
          <Label :for="id('token')" class="flex items-center gap-1.5"><KeyRoundIcon class="size-3.5" /> Token variable</Label>
          <Input :id="id('token')" v-model="source.token_env" class="font-mono text-xs" :placeholder="m.token" :disabled="!editable" />
          <p class="text-xs text-muted-foreground">
            Name of the variable holding the token in the ingester's environment (<code>.env</code>). The token itself never goes here.
          </p>
        </div>
        <div class="space-y-1.5">
          <Label :for="id('user')">User <span class="font-normal text-muted-foreground">(optional)</span></Label>
          <Input :id="id('user')" v-model="source.user" placeholder="bot@example.com" :disabled="!editable" />
          <p class="text-xs text-muted-foreground">Cloud: account email (HTTP Basic). Blank: the token is a Data Center PAT.</p>
        </div>
        <div class="space-y-1.5">
          <Label :for="id('deployment')">Deployment</Label>
          <Select v-model="source.deployment" :disabled="!editable">
            <SelectTrigger :id="id('deployment')" class="w-full"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem v-for="d in DEPLOYMENTS" :key="d" :value="d">{{ d }}</SelectItem>
            </SelectContent>
          </Select>
          <p class="text-xs text-muted-foreground"><code>auto</code>: cloud for *.atlassian.net, Data Center otherwise.</p>
        </div>
        <div v-if="source.type === 'jira'" class="space-y-1.5">
          <Label :for="id('jql')">Extra JQL <span class="font-normal text-muted-foreground">(optional)</span></Label>
          <Input :id="id('jql')" v-model="source.jql" class="font-mono text-xs" placeholder="resolution = Unresolved OR updated >= -180d" :disabled="!editable" />
          <p class="text-xs text-muted-foreground">ANDed to <code>project = KEY</code>; no ORDER BY.</p>
        </div>
      </template>

      <div class="space-y-1.5">
        <Label :for="id('tags')">Tags <span class="font-normal text-muted-foreground">(optional)</span></Label>
        <Input :id="id('tags')" v-model="source.tags" placeholder="acme, handbook" :disabled="!editable" />
        <p class="text-xs text-muted-foreground">Added to every document, next to <code>{{ source.type }}:&lt;scope&gt;</code>.</p>
      </div>
    </CardContent>

    <CardFooter v-if="scopes.length" class="flex flex-wrap gap-2 border-t pt-4">
      <span class="text-xs text-muted-foreground">Indexed:</span>
      <Badge v-for="tag in scopes" :key="tag" variant="outline" class="font-mono text-[11px]">
        {{ tag }}
        <span class="ml-1" :class="indexed?.[tag] ? 'text-foreground' : 'text-muted-foreground'">
          {{ indexed === null ? '…' : indexed[tag] ? `${indexed[tag]} chunks` : 'not synced yet' }}
        </span>
      </Badge>
    </CardFooter>
  </Card>
</template>
