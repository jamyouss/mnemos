<script setup lang="ts">
import { BrainIcon, FlaskConicalIcon, LayoutDashboardIcon, SearchIcon, SlidersHorizontalIcon } from '@lucide/vue'

const route = useRoute()

const groups = [
  {
    label: 'Observe',
    items: [
      { to: '/', label: 'Dashboard', icon: LayoutDashboardIcon },
      { to: '/search', label: 'Search', icon: SearchIcon },
      { to: '/evals', label: 'Evals', icon: FlaskConicalIcon },
    ],
  },
  {
    label: 'Manage',
    items: [
      { to: '/memories', label: 'Memories', icon: BrainIcon },
      { to: '/config', label: 'Configuration', icon: SlidersHorizontalIcon },
    ],
  },
]

const isActive = (to: string) => (to === '/' ? route.path === '/' : route.path.startsWith(to))
</script>

<template>
  <Sidebar collapsible="icon">
    <SidebarHeader>
      <SidebarMenu>
        <SidebarMenuItem>
          <SidebarMenuButton size="lg" as-child>
            <NuxtLink to="/">
              <img src="/logo.svg" alt="" class="size-8 shrink-0">
              <div class="grid flex-1 text-left leading-tight">
                <span class="truncate font-semibold tracking-tight">mnemos</span>
                <span class="truncate text-xs text-muted-foreground">memory layer</span>
              </div>
            </NuxtLink>
          </SidebarMenuButton>
        </SidebarMenuItem>
      </SidebarMenu>
    </SidebarHeader>

    <SidebarContent>
      <SidebarGroup v-for="group in groups" :key="group.label">
        <SidebarGroupLabel>{{ group.label }}</SidebarGroupLabel>
        <SidebarGroupContent>
          <SidebarMenu>
            <SidebarMenuItem v-for="item in group.items" :key="item.to">
              <SidebarMenuButton as-child :is-active="isActive(item.to)" :tooltip="item.label">
                <NuxtLink :to="item.to">
                  <component :is="item.icon" />
                  <span>{{ item.label }}</span>
                </NuxtLink>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarGroupContent>
      </SidebarGroup>
    </SidebarContent>

    <SidebarFooter>
      <ThemeCustomizer />
    </SidebarFooter>
    <SidebarRail />
  </Sidebar>
</template>
