<script setup lang="ts">
import type { Component } from 'vue'
import { InboxIcon } from '@lucide/vue'

/**
 * An empty screen has to say *why* it is empty. "No traffic" and "nothing is
 * being recorded" look identical otherwise — which is how a query logger that
 * had been off for a week went unnoticed.
 */
defineProps<{ title: string; detail?: string; icon?: Component }>()
</script>

<template>
  <div class="flex flex-col items-center rounded-xl border border-dashed p-10 text-center">
    <div class="mb-3 flex size-10 items-center justify-center rounded-full bg-muted">
      <component :is="icon ?? InboxIcon" class="size-5 text-muted-foreground" />
    </div>
    <p class="font-medium">{{ title }}</p>
    <p v-if="detail" class="mx-auto mt-2 max-w-xl text-sm text-muted-foreground">{{ detail }}</p>
    <div v-if="$slots.default" class="mt-4"><slot /></div>
  </div>
</template>
