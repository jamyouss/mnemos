<script setup lang="ts">
/** One tenant scope: every project, or the projects carrying the picked tags. */
import type { ScopeForm } from '~/utils/tenants'

defineProps<{
  id: string
  label: string
  description: string
  options: Array<{ value: string; count?: number }>
  /** What an empty tag selection grants, said where it is chosen. */
  emptyMeaning: string
}>()

const model = defineModel<ScopeForm>({ required: true })

const setAll = (all: boolean) => (model.value = { ...model.value, all })
const tags = computed({
  get: () => model.value.tags,
  set: (value: string) => (model.value = { ...model.value, tags: value }),
})
</script>

<template>
  <fieldset class="space-y-2">
    <legend class="text-sm font-medium">{{ label }}</legend>
    <p class="text-xs text-muted-foreground">{{ description }}</p>
    <RadioGroup
      :model-value="model.all ? 'all' : 'tags'"
      class="flex gap-5"
      @update:model-value="(v) => setAll(v === 'all')"
    >
      <div class="flex items-center gap-2">
        <RadioGroupItem :id="`${id}-all`" value="all" />
        <Label :for="`${id}-all`" class="font-normal">All projects</Label>
      </div>
      <div class="flex items-center gap-2">
        <RadioGroupItem :id="`${id}-tags`" value="tags" />
        <Label :for="`${id}-tags`" class="font-normal">Selected tags</Label>
      </div>
    </RadioGroup>
    <template v-if="!model.all">
      <FacetCombobox :id="`${id}-list`" v-model="tags" :options="options" multiple placeholder="Pick tags…" />
      <p v-if="!tags.trim()" class="text-xs text-muted-foreground">{{ emptyMeaning }}</p>
    </template>
  </fieldset>
</template>
