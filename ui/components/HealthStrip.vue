<script setup lang="ts">
import type { HealthDeps } from '~/composables/useApi'

const props = defineProps<{
  health: HealthDeps | null | undefined
  pending: boolean
  logEnabled: boolean | undefined
}>()

type State = 'ok' | 'down' | 'off' | 'unknown'

const items = computed(() => {
  const h = props.health
  const q = h?.qdrant
  const llm = h?.llm
  return [
    {
      name: 'Qdrant',
      state: (q ? (q.ok ? 'ok' : 'down') : 'unknown') as State,
      detail: q ? (q.ok ? `${Math.round(q.latency_ms ?? 0)} ms` : q.error ?? 'Unreachable') : props.pending ? 'Checking…' : 'Unknown',
    },
    {
      name: 'LLM',
      state: (llm ? (llm.ok ? 'ok' : 'down') : 'unknown') as State,
      detail: llm ? `${llm.provider} · ${llm.model}${llm.ok ? '' : ` — ${llm.error ?? 'unreachable'}`}` : props.pending ? 'Checking…' : 'Unknown',
    },
    {
      name: 'Query log',
      state: (props.logEnabled === undefined ? 'unknown' : props.logEnabled ? 'ok' : 'off') as State,
      detail: props.logEnabled === undefined ? 'Unknown' : props.logEnabled ? 'Recording' : 'Off — no search statistics',
    },
  ]
})

const dot: Record<State, string> = {
  ok: 'bg-success',
  down: 'bg-destructive',
  off: 'bg-warning',
  unknown: 'bg-muted-foreground/40',
}

// The dot's colour is the state; this says it in words.
const stateText: Record<State, string> = {
  ok: 'Up',
  down: 'Down',
  off: 'Off',
  unknown: 'Unknown',
}
</script>

<template>
  <div class="grid grid-cols-1 gap-3 md:grid-cols-3">
    <div v-for="item in items" :key="item.name" class="flex items-center gap-3 rounded-lg border bg-card px-4 py-3 text-sm">
      <span class="relative flex size-2.5 shrink-0" aria-hidden="true">
        <span v-if="item.state === 'ok'" class="absolute inline-flex size-full animate-ping rounded-full bg-success opacity-40 motion-reduce:animate-none" />
        <span class="relative inline-flex size-2.5 rounded-full" :class="dot[item.state]" />
      </span>
      <span class="font-medium">{{ item.name }}<span class="sr-only">: {{ stateText[item.state] }}</span></span>
      <span class="ml-auto line-clamp-2 min-w-0 text-right break-words text-muted-foreground" :title="item.detail">{{ item.detail }}</span>
    </div>
  </div>
</template>
