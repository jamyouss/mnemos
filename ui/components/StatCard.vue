<script setup lang="ts">
import type { Component } from 'vue'

defineProps<{
  label: string
  value: string | number
  hint?: string
  icon?: Component
  tone?: 'default' | 'success' | 'warning' | 'destructive'
}>()

const toneClass = {
  default: 'text-foreground',
  success: 'text-success',
  warning: 'text-warning',
  destructive: 'text-destructive',
}
</script>

<template>
  <Card class="gap-2 py-4">
    <CardHeader class="flex flex-row items-center justify-between gap-2 px-4">
      <CardDescription class="text-xs font-medium uppercase tracking-wide">{{ label }}</CardDescription>
      <component :is="icon" v-if="icon" class="size-4 text-muted-foreground" />
    </CardHeader>
    <CardContent class="px-4">
      <p class="truncate text-2xl font-semibold tabular-nums" :class="toneClass[tone ?? 'default']" :title="String(value)">
        {{ value }}
      </p>
      <p v-if="hint" class="mt-1 truncate text-xs text-muted-foreground" :title="hint">{{ hint }}</p>
    </CardContent>
  </Card>
</template>
