<script setup lang="ts">
import { TriangleAlertIcon } from '@lucide/vue'
import type { EvalRunSummary } from '~/composables/useApi'

definePageMeta({ title: 'Evals' })
useHead({ title: 'Evals' })

const api = useApi()
const { data, error } = await useAsyncData('eval-runs', () => api.evalRuns())

const runs = computed(() => data.value?.runs ?? [])
const selected = ref<string[]>([])

const compared = computed(() =>
  runs.value.filter((r) => (selected.value.length ? selected.value.includes(r.tag) : true)).slice(0, 6),
)

const METRICS = ['mrr', 'ndcg@5', 'recall@5', 'precision@5', 'hit@5'] as const

function value(run: EvalRunSummary, key: string): number | undefined {
  const v = run.report?.overall?.[key]
  return typeof v === 'number' ? v : undefined
}
/** Best value of a row, so the winner of a comparison stands out. */
function best(key: string): number | undefined {
  const vs = compared.value.map((r) => value(r, key)).filter((v): v is number => v !== undefined)
  return compared.value.length > 1 && vs.length ? Math.max(...vs) : undefined
}
function toggle(tag: string) {
  selected.value = selected.value.includes(tag) ? selected.value.filter((t) => t !== tag) : [...selected.value, tag]
}
</script>

<template>
  <div class="space-y-6">
    <PageHeader title="Evals">
      <template #description>
        Runs from <code class="text-xs">evals/runs/</code>. Absolute numbers depend on the golden set; it is the delta
        between two runs on the same set that means something.
      </template>
    </PageHeader>

    <EmptyState v-if="error" title="Could not load eval runs" detail="GET /api/eval/runs failed." />

    <EmptyState
      v-else-if="!runs.length"
      title="No eval runs yet"
      detail="Generate a golden set and run the harness: mnemos eval generate, then mnemos eval run --tag <name>."
    />

    <template v-else>
      <div class="flex flex-wrap gap-2">
        <Button
          v-for="r in runs"
          :key="r.tag"
          size="sm"
          :variant="selected.includes(r.tag) ? 'default' : 'outline'"
          class="font-mono text-xs"
          @click="toggle(r.tag)"
        >
          {{ r.tag }}
        </Button>
      </div>

      <Card class="py-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead class="pl-6">Metric</TableHead>
              <TableHead v-for="r in compared" :key="r.tag" class="font-mono normal-case">{{ r.tag }}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            <TableRow>
              <TableCell class="pl-6 text-muted-foreground">questions</TableCell>
              <TableCell v-for="r in compared" :key="r.tag" class="tabular-nums">{{ r.report?.n_questions ?? '—' }}</TableCell>
            </TableRow>
            <TableRow v-for="m in METRICS" :key="m">
              <TableCell class="pl-6 text-muted-foreground">{{ m }}</TableCell>
              <TableCell
                v-for="r in compared"
                :key="r.tag"
                class="tabular-nums"
                :class="value(r, m) !== undefined && value(r, m) === best(m) && 'font-semibold text-success'"
              >
                {{ value(r, m)?.toFixed(3) ?? '—' }}
              </TableCell>
            </TableRow>
            <TableRow>
              <TableCell class="pl-6 text-muted-foreground">p50</TableCell>
              <TableCell v-for="r in compared" :key="r.tag" class="tabular-nums">
                {{ r.report?.latency_p50_ms ? `${Math.round(r.report.latency_p50_ms)} ms` : '—' }}
              </TableCell>
            </TableRow>
          </TableBody>
        </Table>
      </Card>

      <Alert>
        <TriangleAlertIcon />
        <AlertTitle>The harness cannot measure the grader</AlertTitle>
        <AlertDescription>
          It routes <code>code_search</code> to <code>/api/search-code</code>, which never runs it, and a generated golden
          set is mostly code_search. A grader A/B through the harness returns identical tables — that reads as "no
          effect" when it means "never ran". See <code>docs/EVAL.md</code>.
        </AlertDescription>
      </Alert>
    </template>
  </div>
</template>
