<script setup lang="ts">
/**
 * How a search reached its results: each stage with its time, then every
 * candidate with where it stood on the dense side, the BM25 side, after
 * fusion and after the reranker — kept or dropped.
 */
import type { SearchTrace, TraceStage } from '~/composables/useApi'

const props = defineProps<{ trace: SearchTrace }>()

const maxMs = computed(() => Math.max(1, ...props.trace.stages.map((s) => s.ms ?? 0)))

function details(stage: TraceStage): string {
  return Object.entries(stage)
    .filter(([key, value]) => key !== 'name' && key !== 'ms' && value !== null && value !== undefined)
    .map(([key, value]) => {
      const shown = Array.isArray(value) ? value.join(' · ') || '—' : typeof value === 'object' ? JSON.stringify(value) : String(value)
      return `${key}: ${shown}`
    })
    .join('  ·  ')
}

const rank = (r: number | null | undefined) => (r == null ? '—' : `#${r}`)
const dropped = computed(() => props.trace.candidates.filter((c) => c.final_rank == null).length)
</script>

<template>
  <div class="space-y-6">
    <section class="space-y-2">
      <div class="flex items-baseline justify-between">
        <h3 class="text-sm font-medium">Stages</h3>
        <span class="text-xs tabular-nums text-muted-foreground">{{ Math.round(trace.total_ms) }} ms total</span>
      </div>
      <ul class="space-y-1.5">
        <li v-for="(s, i) in trace.stages" :key="i" class="grid grid-cols-[6rem_1fr_4.5rem] items-center gap-3 text-sm">
          <span class="font-medium">{{ s.name }}</span>
          <div class="relative min-w-0">
            <div
              v-if="s.ms !== null"
              class="absolute inset-y-0 left-0 rounded bg-primary/15"
              :style="{ width: `${Math.max(1, (s.ms / maxMs) * 100)}%` }"
            />
            <p class="relative truncate px-2 py-0.5 text-xs text-muted-foreground" :title="details(s)">{{ details(s) || '—' }}</p>
          </div>
          <span class="text-right text-xs tabular-nums text-muted-foreground">{{ s.ms === null ? '' : `${Math.round(s.ms)} ms` }}</span>
        </li>
      </ul>
    </section>

    <section class="space-y-2">
      <div class="flex items-baseline justify-between">
        <h3 class="text-sm font-medium">Candidates</h3>
        <span class="text-xs text-muted-foreground">
          {{ trace.candidates.length }} retrieved · {{ dropped }} not kept · ranks are within each side's top 20
        </span>
      </div>
      <div class="overflow-x-auto rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead class="w-14">Final</TableHead>
              <TableHead>Chunk</TableHead>
              <TableHead class="text-right">Dense</TableHead>
              <TableHead class="text-right">BM25</TableHead>
              <TableHead class="text-right">Fused</TableHead>
              <TableHead class="text-right">Rerank</TableHead>
              <TableHead>Grade</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            <TableRow
              v-for="(c, i) in trace.candidates"
              :key="i"
              :class="c.final_rank == null && 'text-muted-foreground'"
            >
              <TableCell class="tabular-nums">
                <Badge v-if="c.final_rank != null">#{{ c.final_rank }}</Badge>
                <span v-else class="text-xs">dropped</span>
              </TableCell>
              <TableCell class="max-w-md">
                <p class="truncate font-mono text-xs" :title="c.file_path">{{ c.file_path }}</p>
                <p class="truncate text-xs text-muted-foreground">
                  {{ c.collection.replace(/^mnemos_/, '') }}<span v-if="c.symbol"> · {{ c.symbol }}</span>
                  <span v-if="c.via"> · via “{{ c.via }}”</span>
                </p>
              </TableCell>
              <TableCell class="text-right tabular-nums">{{ rank(c.dense_rank) }}</TableCell>
              <TableCell class="text-right tabular-nums">{{ rank(c.sparse_rank) }}</TableCell>
              <TableCell class="text-right tabular-nums">
                {{ rank(c.fused_rank) }}
                <span class="block text-[11px] text-muted-foreground">{{ c.fused_score.toFixed(3) }}</span>
              </TableCell>
              <TableCell class="text-right tabular-nums">
                {{ rank(c.rerank_rank) }}
                <span v-if="c.rerank_score != null" class="block text-[11px] text-muted-foreground">{{ c.rerank_score.toFixed(2) }}</span>
              </TableCell>
              <TableCell>
                <Badge v-if="c.grade" :variant="c.grade === 'low' ? 'destructive' : 'secondary'">{{ c.grade }}</Badge>
              </TableCell>
            </TableRow>
          </TableBody>
        </Table>
      </div>
    </section>
  </div>
</template>
