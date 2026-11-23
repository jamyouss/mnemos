<script setup lang="ts">
/**
 * Filterable dropdown over indexed facet values. The model stays the
 * comma-separated string the Playground form and its history already hold;
 * `multiple` toggles several values in it. A value the facets do not list
 * (they are capped) can still be typed and picked.
 */
import { CheckIcon, ChevronsUpDownIcon, PlusIcon, XIcon } from '@lucide/vue'
import { splitFilterList } from '~/utils/playground'

export interface FacetOption {
  value: string
  count?: number
}

const props = withDefaults(defineProps<{
  options: FacetOption[]
  multiple?: boolean
  placeholder?: string
  id?: string
}>(), { multiple: false, placeholder: 'Any' })

const model = defineModel<string>({ required: true })
const open = ref(false)
const search = ref('')

const selected = computed(() => splitFilterList(model.value))

const selection = computed({
  get: () => (props.multiple ? selected.value : selected.value[0]),
  set: (value: string | string[] | undefined) => {
    model.value = Array.isArray(value) ? value.join(', ') : (value ?? '')
    search.value = ''
  },
})

// Selected values first, even those the facets do not list, so they stay visible and removable.
const items = computed(() => {
  const counts = new Map(props.options.map((o) => [o.value, o.count]))
  const values = [...new Set([...selected.value, ...props.options.map((o) => o.value)])]
  return values.map((value) => ({ value, count: counts.get(value) }))
})

// Filtered here, not by reka: its filter only follows keystrokes, so clearing
// the term after a pick in `multiple` mode left the list narrowed to it.
const typed = computed(() => search.value.trim())
const visible = computed(() => {
  const term = typed.value.toLowerCase()
  return term ? items.value.filter((i) => i.value.toLowerCase().includes(term)) : items.value
})
const canAddTyped = computed(() => typed.value !== '' && !items.value.some((i) => i.value === typed.value))

function clear(event: Event) {
  event.preventDefault()
  model.value = ''
  search.value = ''
  open.value = false
}
</script>

<template>
  <Combobox v-model="selection" v-model:open="open" :multiple="multiple" ignore-filter>
    <ComboboxAnchor as-child class="w-full">
      <ComboboxTrigger as-child>
        <Button :id="id" type="button" variant="outline" class="h-auto min-h-9 w-full justify-between px-3 py-1.5 font-normal">
          <span v-if="!selected.length" class="truncate text-muted-foreground">{{ placeholder }}</span>
          <span v-else-if="!multiple" class="truncate font-mono text-xs">{{ selected[0] }}</span>
          <span v-else class="flex min-w-0 flex-1 flex-wrap items-center gap-1">
            <Badge
              v-for="v in selected"
              :key="v"
              variant="secondary"
              class="max-w-full justify-start truncate font-mono text-[11px]"
              :title="v"
            >{{ v }}</Badge>
          </span>
          <ChevronsUpDownIcon class="shrink-0 opacity-50" />
        </Button>
      </ComboboxTrigger>
    </ComboboxAnchor>

    <ComboboxList align="start" class="w-(--reka-combobox-trigger-width) min-w-56">
      <ComboboxInput v-model="search" placeholder="Filter…" />
      <ComboboxViewport>
        <ComboboxEmpty>Nothing indexed matches.</ComboboxEmpty>
        <ComboboxGroup>
          <!-- A sentinel value: `@select` cancels the pick and clears instead. -->
          <ComboboxItem v-if="selected.length && !typed" value="__clear__" @select="clear">
            <XIcon /> Clear
          </ComboboxItem>
          <ComboboxItem v-for="item in visible" :key="item.value" :value="item.value">
            <span class="truncate font-mono text-xs">{{ item.value }}</span>
            <span class="ml-auto flex shrink-0 items-center gap-2">
              <span v-if="item.count != null" class="text-xs text-muted-foreground tabular-nums">{{ item.count }}</span>
              <span class="flex size-4 items-center">
                <ComboboxItemIndicator class="ml-0"><CheckIcon /></ComboboxItemIndicator>
              </span>
            </span>
          </ComboboxItem>
          <ComboboxItem v-if="canAddTyped" :value="typed">
            <PlusIcon /> Use <span class="font-mono text-xs">{{ typed }}</span>
          </ComboboxItem>
        </ComboboxGroup>
      </ComboboxViewport>
    </ComboboxList>
  </Combobox>
</template>
