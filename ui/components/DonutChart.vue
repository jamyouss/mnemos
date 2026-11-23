<script setup lang="ts">
/** Share-of-total ring with its own legend; the centre shows the total. */
import { VisDonut, VisSingleContainer } from '@unovis/vue'

const props = defineProps<{
  rows: Array<{ label: string; count: number }>
  centerLabel?: string
  empty?: string
}>()

const PALETTE = ['var(--chart-1)', 'var(--chart-2)', 'var(--chart-3)', 'var(--chart-4)', 'var(--chart-5)']
// Past five slices the colours repeat and the ring stops being readable:
// fold the tail into "Other".
const MAX_SLICES = 5

const slices = computed(() => {
  const sorted = [...props.rows].filter((r) => r.count > 0).sort((a, b) => b.count - a.count)
  if (sorted.length <= MAX_SLICES) return sorted
  const head = sorted.slice(0, MAX_SLICES - 1)
  const other = sorted.slice(MAX_SLICES - 1).reduce((n, r) => n + r.count, 0)
  return [...head, { label: 'Other', count: other }]
})
const total = computed(() => slices.value.reduce((n, r) => n + r.count, 0))
const color = (_: unknown, i: number) => PALETTE[i % PALETTE.length]
const pct = (n: number) => (total.value ? `${Math.round((n / total.value) * 100)}%` : '—')
// The ring is unlabelled SVG; this is its text alternative.
const summary = computed(
  () => `${total.value.toLocaleString()} ${props.centerLabel ?? 'total'}: ${slices.value.map((s) => `${s.label} ${pct(s.count)}`).join(', ')}`,
)
</script>

<template>
  <p v-if="!total" class="py-6 text-sm text-muted-foreground">{{ empty ?? 'Nothing to show yet.' }}</p>
  <!-- Side-by-side only when the card is wide enough; a narrow column stacks
       so the legend labels are not squeezed down to a single letter. -->
  <div v-else class="@container">
    <div class="flex flex-col items-center gap-6 @md:flex-row">
      <div class="relative size-40 shrink-0" role="img" :aria-label="summary">
        <VisSingleContainer :data="slices" class="size-40">
          <VisDonut
            :value="(d: { count: number }) => d.count"
            :color="color"
            :arc-width="22"
            :pad-angle="0.02"
            :corner-radius="3"
            :central-label="total.toLocaleString()"
            :central-sub-label="centerLabel ?? ''"
          />
        </VisSingleContainer>
      </div>
      <ul class="w-full min-w-0 space-y-2 text-sm">
        <li v-for="(s, i) in slices" :key="s.label" class="flex items-center gap-2">
          <span class="size-2.5 shrink-0 rounded-sm" :style="{ background: PALETTE[i % PALETTE.length] }" />
          <span class="min-w-0 flex-1 truncate font-mono text-xs" :title="s.label">{{ s.label }}</span>
          <span class="tabular-nums text-muted-foreground">{{ pct(s.count) }}</span>
          <span class="w-14 text-right tabular-nums">{{ s.count.toLocaleString() }}</span>
        </li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
:deep([data-vis-single-container]) {
  --vis-donut-central-label-text-color: var(--foreground);
  --vis-donut-central-sub-label-text-color: var(--muted-foreground);
  --vis-donut-central-label-font-size: 22px;
  --vis-donut-background-color: var(--muted);
}
</style>
