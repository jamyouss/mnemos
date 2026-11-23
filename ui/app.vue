<script setup lang="ts">
import 'vue-sonner/style.css'
import { MoonIcon, SunIcon } from '@lucide/vue'

const route = useRoute()
const colorMode = useColorMode()

useHead({
  titleTemplate: (title) => (title ? `${title} · mnemos` : 'mnemos'),
  link: [{ rel: 'icon', type: 'image/svg+xml', href: '/logo.svg' }],
})

function toggleMode() {
  colorMode.preference = colorMode.value === 'dark' ? 'light' : 'dark'
}
</script>

<template>
  <!-- Bare pages (sign-in) have no SidebarInset, so the landmark is here. -->
  <main v-if="route.meta.bare">
    <NuxtPage />
    <Toaster rich-colors close-button />
  </main>
  <SidebarProvider v-else>
    <AppSidebar />
    <!-- min-w-0: a flex child otherwise grows to its widest content, and one
         long unbroken line (a path, a code token) pushes the page sideways. -->
    <SidebarInset class="min-w-0">
      <header class="sticky top-0 z-10 flex h-14 shrink-0 items-center gap-2 border-b bg-background/80 px-4 backdrop-blur">
        <SidebarTrigger class="-ml-1" />
        <Separator orientation="vertical" class="mr-2 data-[orientation=vertical]:h-4" />
        <span class="text-sm font-medium">{{ route.meta.title ?? 'mnemos' }}</span>
        <Button
          variant="ghost"
          size="icon"
          class="ml-auto size-8"
          aria-label="Dark mode"
          :aria-pressed="colorMode.value === 'dark'"
          @click="toggleMode"
        >
          <SunIcon class="hidden dark:block" />
          <MoonIcon class="dark:hidden" />
        </Button>
      </header>
      <!-- SidebarInset is already the page's <main>. -->
      <div class="mx-auto w-full max-w-7xl flex-1 p-4 md:p-8">
        <NuxtPage />
      </div>
    </SidebarInset>
    <Toaster rich-colors close-button />
  </SidebarProvider>
</template>
