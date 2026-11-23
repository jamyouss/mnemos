<script setup lang="ts">
/** "x–y of N", page links and a page-size picker, for any paged list. */
const props = withDefaults(defineProps<{ total: number; sizes?: readonly number[] }>(), {
  sizes: () => [25, 50, 100],
})
const page = defineModel<number>('page', { required: true })
const pageSize = defineModel<number>('pageSize', { required: true })

const first = computed(() => (props.total ? (page.value - 1) * pageSize.value + 1 : 0))
const last = computed(() => Math.min(page.value * pageSize.value, props.total))
</script>

<template>
  <div v-if="total" class="flex flex-wrap items-center justify-between gap-4 text-sm text-muted-foreground">
    <span class="tabular-nums" aria-live="polite">{{ first.toLocaleString() }}–{{ last.toLocaleString() }} of {{ total.toLocaleString() }}</span>
    <Pagination
      v-if="total > pageSize"
      v-model:page="page"
      :total="total"
      :items-per-page="pageSize"
      :sibling-count="1"
      show-edges
      class="mx-0 w-auto max-w-full"
      aria-label="Pages"
    >
      <PaginationContent v-slot="{ items }" class="flex-wrap">
        <PaginationPrevious />
        <template v-for="(item, index) in items" :key="index">
          <PaginationItem v-if="item.type === 'page'" :value="item.value" :is-active="item.value === page">
            {{ item.value }}
          </PaginationItem>
          <PaginationEllipsis v-else :index="index" />
        </template>
        <PaginationNext />
      </PaginationContent>
    </Pagination>
    <Select :model-value="String(pageSize)" @update:model-value="pageSize = Number($event)">
      <SelectTrigger size="sm" class="w-32" aria-label="Items per page"><SelectValue /></SelectTrigger>
      <SelectContent>
        <SelectItem v-for="s in sizes" :key="s" :value="String(s)">{{ s }} per page</SelectItem>
      </SelectContent>
    </Select>
  </div>
</template>
