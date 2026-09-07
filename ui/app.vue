<script setup lang="ts">
const nav = [
  { to: '/', label: 'System' },
  { to: '/search', label: 'Search' },
  { to: '/memories', label: 'Memories' },
  { to: '/evals', label: 'Evals' },
]
useHead({ title: 'Mnemos' })
</script>

<template>
  <div class="min-h-screen">
    <header class="sticky top-0 z-10 border-b bg-background/80 backdrop-blur">
      <div class="mx-auto flex max-w-6xl items-center gap-6 px-6 py-3">
        <NuxtLink to="/" class="font-semibold tracking-tight">mnemos</NuxtLink>
        <nav class="flex gap-1 text-sm">
          <NuxtLink
            v-for="item in nav"
            :key="item.to"
            :to="item.to"
            class="rounded-md px-3 py-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"
            active-class="bg-muted !text-foreground"
          >
            {{ item.label }}
          </NuxtLink>
        </nav>
      </div>
    </header>
    <main class="mx-auto max-w-6xl px-6 py-8">
      <NuxtPage />
    </main>
  </div>
</template>
