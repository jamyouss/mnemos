<script setup lang="ts">
/**
 * Ranked horizontal bars, scaled to the largest row. Labels sit inside the
 * bar so long file paths stay readable instead of being squeezed into an axis.
 */
const props = defineProps<{
  rows: Array<{ label: string; count: number }>
  empty?: string
  mono?: boolean
}>()

const max = computed(() => Math.max(1, ...props.rows.map((r) => r.count)))
</script>

<template>
  <p v-if="!rows.length" class="py-6 text-sm text-muted-foreground">
    {{ empty ?? 'Nothing to show yet.' }}
  </p>
  <ul v-else class="space-y-1.5">
    <li v-for="row in rows" :key="row.label" class="flex items-center gap-3 text-sm">
      <div class="relative h-7 min-w-0 flex-1">
        <div
          class="absolute inset-y-0 left-0 rounded-md bg-primary/15"
          :style="{ width: `${Math.max(1, (row.count / max) * 100)}%` }"
          aria-hidden="true"
        />
        <!-- block, not flex: text-overflow only puts the ellipsis on a block's own text. -->
        <span
          class="relative block truncate px-2 leading-7"
          :class="mono && 'font-mono text-xs'"
          :title="row.label"
        >{{ row.label }}</span>
      </div>
      <span class="w-12 shrink-0 text-right tabular-nums text-muted-foreground">{{ row.count.toLocaleString() }}</span>
    </li>
  </ul>
</template>
