<script setup lang="ts">
import type { Component } from 'vue'
import { CircleAlertIcon, InboxIcon } from '@lucide/vue'

/**
 * An empty screen has to say *why* it is empty. "No traffic" and "nothing is
 * being recorded" look identical otherwise — which is how a query logger that
 * had been off for a week went unnoticed.
 *
 * `tone="error"` is for a failed load: it is announced at once (`alert`),
 * where a plain empty state waits its turn (`status`).
 */
defineProps<{ title: string; detail?: string; icon?: Component; tone?: 'error' }>()
</script>

<template>
  <div
    :role="tone === 'error' ? 'alert' : 'status'"
    class="flex flex-col items-center rounded-xl border border-dashed p-10 text-center"
    :class="tone === 'error' && 'border-destructive/50'"
  >
    <div class="mb-3 flex size-10 items-center justify-center rounded-full" :class="tone === 'error' ? 'bg-destructive/10' : 'bg-muted'">
      <component
        :is="icon ?? (tone === 'error' ? CircleAlertIcon : InboxIcon)"
        class="size-5"
        :class="tone === 'error' ? 'text-destructive' : 'text-muted-foreground'"
        aria-hidden="true"
      />
    </div>
    <p class="font-medium">{{ title }}</p>
    <p v-if="detail" class="mx-auto mt-2 max-w-xl text-sm text-muted-foreground">{{ detail }}</p>
    <div v-if="$slots.default" class="mt-4"><slot /></div>
  </div>
</template>
