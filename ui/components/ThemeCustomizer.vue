<script setup lang="ts">
import { CheckIcon, MonitorIcon, MoonIcon, PaletteIcon, SunIcon } from '@lucide/vue'
import { PRESETS, RADII } from '~/utils/theme'

const colorMode = useColorMode()
const { prefs, set } = useThemePrefs()

const modes = [
  { value: 'light', label: 'Light', icon: SunIcon },
  { value: 'dark', label: 'Dark', icon: MoonIcon },
  { value: 'system', label: 'System', icon: MonitorIcon },
]
</script>

<template>
  <Sheet>
    <SidebarMenu>
      <SidebarMenuItem>
        <SheetTrigger as-child>
          <SidebarMenuButton tooltip="Appearance">
            <PaletteIcon />
            <span>Appearance</span>
          </SidebarMenuButton>
        </SheetTrigger>
      </SidebarMenuItem>
    </SidebarMenu>

    <SheetContent side="right" class="w-full gap-0 sm:max-w-sm">
      <SheetHeader>
        <SheetTitle>Appearance</SheetTitle>
        <SheetDescription>
          Saved in this browser only. "Instance" keeps the theme your deployment ships in
          <code class="text-xs">config/theme.css</code>.
        </SheetDescription>
      </SheetHeader>

      <div class="space-y-6 px-4 pb-6">
        <section class="space-y-2">
          <Label>Mode</Label>
          <div class="grid grid-cols-3 gap-2">
            <Button
              v-for="m in modes"
              :key="m.value"
              variant="outline"
              size="sm"
              :class="colorMode.preference === m.value && 'border-primary ring-1 ring-primary'"
              @click="colorMode.preference = m.value"
            >
              <component :is="m.icon" />
              {{ m.label }}
            </Button>
          </div>
        </section>

        <section class="space-y-2">
          <Label>Accent</Label>
          <div class="grid grid-cols-2 gap-2">
            <Button
              variant="outline"
              size="sm"
              class="justify-start"
              :class="prefs.preset === null && 'border-primary ring-1 ring-primary'"
              @click="set({ preset: null })"
            >
              <span class="size-3.5 rounded-full border bg-foreground" />
              Instance
              <CheckIcon v-if="prefs.preset === null" class="ml-auto" />
            </Button>
            <Button
              v-for="p in PRESETS"
              :key="p.id"
              variant="outline"
              size="sm"
              class="justify-start"
              :class="prefs.preset === p.id && 'border-primary ring-1 ring-primary'"
              @click="set({ preset: p.id })"
            >
              <span class="size-3.5 rounded-full" :style="{ background: p.swatch }" />
              {{ p.label }}
              <CheckIcon v-if="prefs.preset === p.id" class="ml-auto" />
            </Button>
          </div>
        </section>

        <section class="space-y-2">
          <Label>Radius</Label>
          <div class="grid grid-cols-4 gap-2">
            <Button
              variant="outline"
              size="sm"
              :class="prefs.radius === null && 'border-primary ring-1 ring-primary'"
              @click="set({ radius: null })"
            >
              Instance
            </Button>
            <Button
              v-for="r in RADII"
              :key="r"
              variant="outline"
              size="sm"
              :class="prefs.radius === r && 'border-primary ring-1 ring-primary'"
              @click="set({ radius: r })"
            >
              {{ r }}
            </Button>
          </div>
        </section>
      </div>
    </SheetContent>
  </Sheet>
</template>
