<script setup lang="ts">
import type { Component } from 'vue'
import { BrainIcon, FlaskConicalIcon, FolderTreeIcon, LayoutDashboardIcon, PlugIcon, SearchIcon, SlidersHorizontalIcon, UsersIcon } from '@lucide/vue'
import { canReadSetup, isAdmin, type Whoami } from '~/utils/tenants'

const route = useRoute()
const { data: me } = useWhoami()

interface NavItem {
  to: string
  label: string
  icon: Component
  /** Who may open it; absent: everyone. */
  allowed?: (me: Whoami | null | undefined) => boolean
}

const groups: { label: string; items: NavItem[] }[] = [
  {
    label: 'Observe',
    items: [
      { to: '/', label: 'Dashboard', icon: LayoutDashboardIcon },
      { to: '/search', label: 'Search', icon: SearchIcon, allowed: isAdmin },
      { to: '/playground', label: 'Playground', icon: FlaskConicalIcon },
    ],
  },
  {
    label: 'Manage',
    items: [
      { to: '/memories', label: 'Memories', icon: BrainIcon },
      { to: '/connectors', label: 'Connectors', icon: PlugIcon, allowed: canReadSetup },
      { to: '/projects', label: 'Projects', icon: FolderTreeIcon, allowed: canReadSetup },
      { to: '/tenants', label: 'Tenants', icon: UsersIcon, allowed: isAdmin },
      { to: '/config', label: 'Configuration', icon: SlidersHorizontalIcon, allowed: isAdmin },
    ],
  },
]

const visibleGroups = computed(() =>
  groups.map((g) => ({ ...g, items: g.items.filter((item) => !item.allowed || item.allowed(me.value)) })),
)

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
      <SidebarGroup v-for="group in visibleGroups" :key="group.label">
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
      <p v-if="me?.auth_enabled" class="truncate px-2 text-xs text-muted-foreground group-data-[collapsible=icon]:hidden" :title="me.name">
        Signed in as <span class="font-medium text-foreground">{{ me.name }}</span>{{ me.admin ? ' · admin' : '' }}
      </p>
      <ThemeCustomizer />
    </SidebarFooter>
    <SidebarRail />
  </Sidebar>
</template>
