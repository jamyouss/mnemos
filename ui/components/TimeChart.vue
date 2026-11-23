<script setup lang="ts">
/**
 * One chart for every time series on the dashboard: stacked areas for
 * volumes, plain lines for latencies. Series colours come from the theme's
 * --chart-N tokens, so an instance theme recolours them with no code change.
 */
import type { ChartConfig } from '@/components/ui/chart'
import { VisArea, VisAxis, VisLine, VisXYContainer } from '@unovis/vue'
import { ChartContainer, ChartCrosshair, ChartLegendContent, ChartTooltip, ChartTooltipContent, componentToString } from '@/components/ui/chart'

type Row = { t: number } & Record<string, number>

const props = defineProps<{
  data: Row[]
  series: Array<{ key: string; label: string }>
  kind?: 'area' | 'line'
  bucket: 'hour' | 'day'
  unit?: string
}>()

const PALETTE = ['var(--chart-1)', 'var(--chart-2)', 'var(--chart-3)', 'var(--chart-4)', 'var(--chart-5)']

const config = computed<ChartConfig>(() =>
  Object.fromEntries(props.series.map((s, i) => [s.key, { label: s.label, color: PALETTE[i % PALETTE.length] }])),
)
const colors = computed(() => props.series.map((s) => config.value[s.key]!.color as string))
const accessors = computed(() => props.series.map((s) => (d: Row) => d[s.key] ?? 0))

function formatTick(t: number | Date): string {
  const d = new Date(t)
  return props.bucket === 'hour'
    ? d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
    : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

function formatLabel(t: number | Date): string {
  const d = new Date(t)
  return props.bucket === 'hour'
    ? d.toLocaleString(undefined, { weekday: 'short', hour: '2-digit', minute: '2-digit' })
    : d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })
}

// componentToString allocates an id, so it must run once in setup, not per render.
const template = componentToString(config.value, ChartTooltipContent, { labelFormatter: formatLabel })
const yTick = (v: number) => `${Math.round(v).toLocaleString()}${props.unit ?? ''}`

// The plot is unlabelled SVG: say what it shows, per series, in one sentence each.
const summary = computed(() => {
  const rows = props.data
  if (!rows.length) return 'No data'
  const range = `${formatLabel(rows[0]!.t)} to ${formatLabel(rows[rows.length - 1]!.t)}`
  const parts = props.series.map((s) => {
    let peak = rows[0]!
    let sum = 0
    for (const r of rows) {
      sum += r[s.key] ?? 0
      if ((r[s.key] ?? 0) > (peak[s.key] ?? 0)) peak = r
    }
    const atPeak = `peak ${yTick(peak[s.key] ?? 0)} on ${formatLabel(peak.t)}`
    return (props.kind ?? 'area') === 'area'
      ? `${s.label}: ${yTick(sum)} in total, ${atPeak}`
      : `${s.label}: ${atPeak}, latest ${yTick(rows[rows.length - 1]![s.key] ?? 0)}`
  })
  return [range, ...parts].join('. ')
})
</script>

<template>
  <ChartContainer :config="config" class="aspect-auto h-64 w-full" cursor role="img" :aria-label="summary">
    <VisXYContainer :data="data" :margin="{ left: 4, right: 4 }" :y-domain="[0, undefined]">
      <VisArea
        v-if="(kind ?? 'area') === 'area'"
        :x="(d: Row) => d.t"
        :y="accessors"
        :color="colors"
        :opacity="0.7"
      />
      <VisLine v-else :x="(d: Row) => d.t" :y="accessors" :color="colors" :line-width="2" />
      <VisAxis
        type="x"
        :x="(d: Row) => d.t"
        :tick-line="false"
        :domain-line="false"
        :grid-line="false"
        :num-ticks="6"
        :tick-format="formatTick"
      />
      <VisAxis type="y" :num-ticks="4" :tick-line="false" :domain-line="false" :tick-format="yTick" />
      <ChartTooltip />
      <ChartCrosshair :template="template" :color="colors" />
    </VisXYContainer>
    <ChartLegendContent v-if="series.length > 1" />
  </ChartContainer>
</template>
