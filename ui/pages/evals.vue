<script setup lang="ts">
const api = useApi()
const { data, error } = await useAsyncData('eval-runs', () => api.evalRuns())

const runs = computed(() => data.value?.runs ?? [])
const selected = ref<string[]>([])

const compared = computed(() =>
  runs.value.filter((r) => (selected.value.length ? selected.value.includes(r.tag) : true)).slice(0, 6),
)

const METRICS = ['mrr', 'ndcg@5', 'recall@5', 'precision@5', 'hit@5'] as const

function metric(run: any, key: string): string {
  const v = run?.report?.overall?.[key]
  return typeof v === 'number' ? v.toFixed(3) : '—'
}
function toggle(tag: string) {
  selected.value = selected.value.includes(tag)
    ? selected.value.filter((t) => t !== tag)
    : [...selected.value, tag]
}
</script>

<template>
  <div class="space-y-6">
    <div>
      <h1 class="text-2xl font-semibold tracking-tight">Evals</h1>
      <p class="mt-1 text-sm text-muted-foreground">
        Runs from <code class="text-xs">evals/runs/</code>. Absolute numbers depend on the golden set;
        it is the delta between two runs on the same set that means something.
      </p>
    </div>

    <EmptyState v-if="error" title="Could not load eval runs" detail="GET /api/eval/runs failed." />

    <EmptyState
      v-else-if="!runs.length"
      title="No eval runs yet"
      detail="Generate a golden set and run the harness: mnemos eval generate, then mnemos eval run --tag <name>."
    />

    <template v-else>
      <div class="flex flex-wrap gap-2">
        <button
          v-for="r in runs"
          :key="r.tag"
          class="rounded-md border px-2.5 py-1 text-xs"
          :class="selected.includes(r.tag) ? 'bg-primary text-primary-foreground' : 'hover:bg-muted'"
          @click="toggle(r.tag)"
        >{{ r.tag }}</button>
      </div>

      <div class="overflow-x-auto rounded-lg border">
        <table class="w-full text-sm">
          <thead class="bg-muted/50 text-left text-xs uppercase text-muted-foreground">
            <tr>
              <th class="px-4 py-2">Metric</th>
              <th v-for="r in compared" :key="r.tag" class="px-4 py-2 font-mono normal-case">{{ r.tag }}</th>
            </tr>
          </thead>
          <tbody>
            <tr class="border-t">
              <td class="px-4 py-2 text-muted-foreground">questions</td>
              <td v-for="r in compared" :key="r.tag" class="px-4 py-2 tabular-nums">
                {{ r.report?.n_questions ?? '—' }}
              </td>
            </tr>
            <tr v-for="m in METRICS" :key="m" class="border-t">
              <td class="px-4 py-2 text-muted-foreground">{{ m }}</td>
              <td v-for="r in compared" :key="r.tag" class="px-4 py-2 tabular-nums">{{ metric(r, m) }}</td>
            </tr>
            <tr class="border-t">
              <td class="px-4 py-2 text-muted-foreground">p50</td>
              <td v-for="r in compared" :key="r.tag" class="px-4 py-2 tabular-nums">
                {{ r.report?.latency_p50_ms ? `${Math.round(r.report.latency_p50_ms)} ms` : '—' }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
        <strong class="text-foreground">The harness cannot measure the grader.</strong>
        It routes <code class="text-xs">code_search</code> to
        <code class="text-xs">/api/search-code</code>, which never runs it, and a generated golden set is
        mostly code_search. A grader A/B through the harness returns identical tables — that reads as
        "no effect" when it means "never ran". See <code class="text-xs">docs/EVAL.md</code>.
      </div>
    </template>
  </div>
</template>
