<script setup lang="ts">
defineProps<{
  label: string
  value: string | number
  hint?: string
  tone?: 'default' | 'success' | 'warning' | 'destructive'
}>()

const toneClass = {
  default: 'text-foreground',
  success: 'text-[hsl(var(--success))]',
  warning: 'text-[hsl(var(--warning))]',
  destructive: 'text-destructive',
}
</script>

<template>
  <div class="rounded-lg border bg-card p-4">
    <p class="text-xs uppercase tracking-wide text-muted-foreground">{{ label }}</p>
    <p class="mt-1 text-2xl font-semibold tabular-nums" :class="toneClass[tone ?? 'default']">
      {{ value }}
    </p>
    <p v-if="hint" class="mt-1 text-xs text-muted-foreground">{{ hint }}</p>
  </div>
</template>
