<script setup lang="ts">
/**
 * What the server says needs attention, under the top bar of every page.
 * A failed poll shows nothing: the banner must never become the problem.
 */
import { CircleAlertIcon, TriangleAlertIcon, XIcon } from '@lucide/vue'
import { useIntervalFn } from '@vueuse/core'
import type { ServerAlert } from '~/composables/useApi'
import { DISMISSED_ALERTS_STORAGE, parseDismissed, pruneDismissed, visibleAlerts } from '~/utils/alerts'

const api = useApi()
const route = useRoute()
const alerts = ref<ServerAlert[]>([])
const dismissed = ref<string[]>(parseDismissed(sessionStorage.getItem(DISMISSED_ALERTS_STORAGE)))
const expanded = ref(false)

function saveDismissed(ids: string[]) {
  dismissed.value = ids
  sessionStorage.setItem(DISMISSED_ALERTS_STORAGE, JSON.stringify(ids))
}

async function poll() {
  try {
    const { alerts: current } = await api.alerts()
    alerts.value = current
    saveDismissed(pruneDismissed(dismissed.value, current.map((a) => a.id)))
  } catch {
    alerts.value = []
  }
}

useIntervalFn(poll, 60_000, { immediateCallback: true })
watch(() => route.path, poll)

const shown = computed(() => visibleAlerts(alerts.value, dismissed.value))
const collapsed = computed(() => shown.value.length > 2 && !expanded.value)
const dismiss = (id: string) => saveDismissed([...dismissed.value, id])
const anyError = computed(() => shown.value.some((a) => a.severity === 'error'))
const isExternal = (href: string) => /^https?:\/\//.test(href)
</script>

<template>
  <section v-if="shown.length" aria-label="Server alerts" class="space-y-2 border-b px-4 py-3 md:px-8">
    <div
      v-if="collapsed"
      role="alert"
      class="flex items-center gap-3 rounded-lg border bg-card px-4 py-2.5 text-sm"
      :class="anyError ? 'text-destructive' : 'border-warning/50 text-warning'"
    >
      <CircleAlertIcon v-if="anyError" class="size-4 shrink-0" aria-hidden="true" />
      <TriangleAlertIcon v-else class="size-4 shrink-0" aria-hidden="true" />
      <span class="min-w-0 flex-1 truncate font-medium">{{ shown[0]!.title }}</span>
      <Button variant="link" size="sm" class="h-auto shrink-0 p-0 text-current underline" :aria-expanded="false" @click="expanded = true">
        {{ shown.length - 1 }} more
      </Button>
    </div>
    <template v-else>
      <Alert
        v-for="a in shown"
        :key="a.id"
        :variant="a.severity === 'error' ? 'destructive' : 'default'"
        :class="a.severity === 'warning' && 'border-warning/50 text-warning *:data-[slot=alert-description]:text-warning/90'"
        class="pr-10"
      >
        <CircleAlertIcon v-if="a.severity === 'error'" aria-hidden="true" />
        <TriangleAlertIcon v-else aria-hidden="true" />
        <AlertTitle>{{ a.title }}</AlertTitle>
        <AlertDescription>
          <p>
            {{ a.detail }}
            <template v-if="a.href">
              <a v-if="isExternal(a.href)" :href="a.href" target="_blank" rel="noopener" class="font-medium underline">Details</a>
              <NuxtLink v-else :to="a.href" class="font-medium underline">Details</NuxtLink>
            </template>
          </p>
        </AlertDescription>
        <Button
          variant="ghost"
          size="icon"
          class="absolute top-2 right-2 size-7 text-current"
          :aria-label="`Dismiss: ${a.title}`"
          @click="dismiss(a.id)"
        >
          <XIcon />
        </Button>
      </Alert>
      <Button v-if="shown.length > 2" variant="link" size="sm" class="h-auto p-0" :aria-expanded="true" @click="expanded = false">
        Show less
      </Button>
    </template>
  </section>
</template>
