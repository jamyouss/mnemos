<script setup lang="ts">
import type { Component } from 'vue'
import { BrainIcon, FlaskConicalIcon, FolderTreeIcon, LayoutDashboardIcon, LogOutIcon, PlugIcon, SearchIcon, SlidersHorizontalIcon, UsersIcon } from '@lucide/vue'
import { signOut } from '~/composables/useSession'
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

// Shown only when it says something the name does not.
const role = computed(() => (me.value?.admin ? 'admin' : 'member'))
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
      <div v-if="me?.auth_enabled" class="flex items-center gap-1 pl-2 group-data-[collapsible=icon]:hidden">
        <p class="min-w-0 flex-1 truncate text-xs text-muted-foreground" :title="`${me.name} (${role})`">
          Signed in as <span class="font-medium text-foreground">{{ me.name }}</span>
          <Badge v-if="role !== me.name" variant="secondary" class="ml-1 px-1.5 text-[10px]">{{ role }}</Badge>
        </p>
        <Button variant="ghost" size="icon" class="size-7 shrink-0" aria-label="Sign out" title="Sign out" @click="signOut">
          <LogOutIcon />
        </Button>
      </div>
      <ThemeCustomizer />
    </SidebarFooter>
    <SidebarRail />
  </Sidebar>
</template>
