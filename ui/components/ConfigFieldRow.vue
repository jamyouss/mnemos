<script lang="ts">
/** Visible names of `ConfigField.apply`, shared with the config review dialog. */
export const APPLY_LABEL = { live: 'Live', restart: 'After restart', reindex: 'Needs reindex' } as const
</script>

<script setup lang="ts">
import { LockIcon, RotateCcwIcon } from '@lucide/vue'
import type { ConfigField } from '~/composables/useApi'

const props = defineProps<{
  field: ConfigField
  modelValue: unknown
  writable: boolean
  dirty: boolean
  error?: string
  pendingRestart: boolean
}>()

const emit = defineEmits<{ 'update:modelValue': [value: unknown]; reset: [] }>()

const editable = computed(() => props.writable && props.field.editable)
const isNumber = computed(() => props.field.type === 'int' || props.field.type === 'float')
const step = computed(() => (props.field.type === 'int' ? 1 : 0.01))

const sourceVariant = { default: 'outline', env: 'secondary', override: 'default' } as const
const sourceLabel = { default: 'Default', env: 'Environment', override: 'Dashboard' } as const
const sourceHint = {
  default: 'Built-in default',
  env: 'Set by an environment variable on the server',
  override: 'Saved from this dashboard — wins over the environment',
} as const
const applyHint = {
  live: 'Takes effect immediately',
  restart: 'Saved now, applies after a server restart',
  reindex: 'Needs a full reindex — cannot be changed here',
} as const

const errorId = computed(() => `${props.field.key}-error`)
const describedBy = computed(() => (props.error ? errorId.value : undefined))

function onNumber(v: string | number) {
  const n = typeof v === 'number' ? v : Number(v)
  emit('update:modelValue', Number.isNaN(n) ? v : n)
}

const display = (v: unknown) => (v === '' || v === null || v === undefined ? '—' : String(v))
</script>

<template>
  <div class="grid gap-3 py-4 md:grid-cols-[minmax(0,1fr)_minmax(0,18rem)] md:gap-8" :class="dirty && 'rounded-md bg-primary/5 -mx-3 px-3'">
    <div class="min-w-0 space-y-1.5">
      <div class="flex flex-wrap items-center gap-2">
        <Label :for="field.key" class="text-sm font-medium">{{ field.label }}</Label>
        <Tooltip>
          <TooltipTrigger as-child>
            <Badge :variant="sourceVariant[field.source]" tabindex="0" class="cursor-default text-[10px]">{{ sourceLabel[field.source] }}</Badge>
          </TooltipTrigger>
          <TooltipContent>{{ sourceHint[field.source] }}</TooltipContent>
        </Tooltip>
        <Tooltip v-if="field.editable">
          <TooltipTrigger as-child>
            <Badge variant="outline" tabindex="0" class="cursor-default text-[10px]" :class="field.apply !== 'live' && 'border-warning text-warning'">
              {{ APPLY_LABEL[field.apply] }}
            </Badge>
          </TooltipTrigger>
          <TooltipContent>{{ applyHint[field.apply] }}</TooltipContent>
        </Tooltip>
        <Badge v-if="pendingRestart" variant="outline" class="border-warning text-[10px] text-warning">Restart pending</Badge>
      </div>
      <p class="text-sm text-muted-foreground">{{ field.description }}</p>
      <p class="font-mono text-[11px] text-muted-foreground">
        {{ field.env }}<span v-if="field.type !== 'secret'"> · default {{ display(field.default) }}</span>
      </p>
      <p v-if="error" :id="errorId" role="alert" class="text-sm text-destructive">{{ error }}</p>
    </div>

    <div class="flex items-start gap-2 md:justify-end">
      <template v-if="field.type === 'secret'">
        <Badge :variant="field.is_set ? 'secondary' : 'outline'">
          <LockIcon class="size-3" /><span class="sr-only">Secret:</span> {{ field.is_set ? 'Set' : 'Not set' }}
        </Badge>
      </template>

      <template v-else-if="field.type === 'bool'">
        <Switch :id="field.key" :model-value="modelValue === true" :disabled="!editable" :aria-describedby="describedBy" @update:model-value="emit('update:modelValue', $event)" />
      </template>

      <template v-else-if="field.type === 'enum' && field.options">
        <Select :model-value="String(modelValue)" :disabled="!editable" @update:model-value="emit('update:modelValue', $event)">
          <SelectTrigger :id="field.key" class="w-full md:w-56" :aria-describedby="describedBy" :aria-invalid="!!error"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem v-for="o in field.options" :key="o" :value="o">{{ o }}</SelectItem>
          </SelectContent>
        </Select>
      </template>

      <template v-else-if="isNumber">
        <Input
          :id="field.key"
          type="number"
          class="w-full md:w-40"
          :model-value="modelValue as number"
          :min="field.min ?? undefined"
          :max="field.max ?? undefined"
          :step="step"
          :disabled="!editable"
          :aria-invalid="!!error"
          :aria-describedby="describedBy"
          @update:model-value="onNumber"
        />
      </template>

      <template v-else-if="editable">
        <Input
          :id="field.key"
          class="w-full md:w-56"
          :model-value="modelValue as string"
          :aria-invalid="!!error"
          :aria-describedby="describedBy"
          @update:model-value="emit('update:modelValue', $event)"
        />
      </template>

      <code v-else :id="field.key" class="max-w-full truncate rounded bg-muted px-2 py-1 text-xs" :title="display(modelValue)">{{ display(modelValue) }}</code>

      <Tooltip v-if="writable && field.source === 'override'">
        <TooltipTrigger as-child>
          <Button variant="ghost" size="icon" class="size-8 shrink-0" :aria-label="`Reset ${field.label}`" @click="emit('reset')">
            <RotateCcwIcon />
          </Button>
        </TooltipTrigger>
        <TooltipContent>Reset — back to the environment or default value</TooltipContent>
      </Tooltip>
    </div>
  </div>
</template>
