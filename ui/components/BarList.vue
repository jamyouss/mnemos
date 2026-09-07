<script setup lang="ts">
/** Horizontal bars scaled to the largest row — readable without a chart lib. */
const props = defineProps<{
  rows: Array<{ label: string; count: number }>
  empty?: string
}>()

const max = computed(() => Math.max(1, ...props.rows.map((r) => r.count)))
</script>

<template>
  <div v-if="!rows.length" class="py-6 text-sm text-muted-foreground">
    {{ empty ?? 'Nothing to show yet.' }}
  </div>
  <div v-else class="space-y-1.5">
    <div v-for="row in rows" :key="row.label" class="flex items-center gap-3 text-sm">
      <span class="w-16 shrink-0 text-right tabular-nums text-muted-foreground">{{ row.count }}</span>
      <div class="h-5 min-w-[2px] rounded bg-primary/80" :style="{ width: `${(row.count / max) * 55}%` }" />
      <span class="truncate font-mono text-xs text-muted-foreground" :title="row.label">{{ row.label }}</span>
    </div>
  </div>
</template>
