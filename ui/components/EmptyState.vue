<script setup lang="ts">
/**
 * An empty screen has to say *why* it is empty. "No traffic" and "nothing is
 * being recorded" look identical otherwise — which is how a query logger that
 * had been off for a week went unnoticed.
 */
defineProps<{ title: string; detail?: string }>()
</script>

<template>
  <div class="rounded-lg border border-dashed p-8 text-center">
    <p class="font-medium">{{ title }}</p>
    <p v-if="detail" class="mx-auto mt-2 max-w-xl text-sm text-muted-foreground">{{ detail }}</p>
    <slot />
  </div>
</template>
