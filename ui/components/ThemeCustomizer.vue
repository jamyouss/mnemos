<script setup lang="ts">
import type { AcceptableValue } from 'reka-ui'
import { CheckIcon, MonitorIcon, MoonIcon, PaletteIcon, SunIcon } from '@lucide/vue'
import { RadioGroupItem, RadioGroupRoot } from 'reka-ui'
import { PRESETS, RADII, type PresetId } from '~/utils/theme'

const colorMode = useColorMode()
const { prefs, set } = useThemePrefs()

const modes = [
  { value: 'light', label: 'Light', icon: SunIcon },
  { value: 'dark', label: 'Dark', icon: MoonIcon },
  { value: 'system', label: 'System', icon: MonitorIcon },
]

const RADIUS_LABELS: Record<(typeof RADII)[number], string> = {
  0: 'None',
  0.3: 'XS',
  0.5: 'S',
  0.625: 'M',
  0.75: 'L',
  1: 'XL',
}

// Radio values are strings; `null` (the organisation's theme) travels as 'default'.
const DEFAULT = 'default'

const preset = computed(() => prefs.value.preset ?? DEFAULT)
const radius = computed(() => (prefs.value.radius === null ? DEFAULT : String(prefs.value.radius)))

function setMode(v: AcceptableValue) {
  colorMode.preference = String(v)
}
function setPreset(v: AcceptableValue) {
  set({ preset: v === DEFAULT ? null : (v as PresetId) })
}
function setRadius(v: AcceptableValue) {
  set({ radius: v === DEFAULT ? null : Number(v) })
}

const selected = 'data-[state=checked]:border-primary data-[state=checked]:ring-1 data-[state=checked]:ring-primary'
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
        <SheetDescription>Saved in this browser only. “Default” keeps your organisation's theme.</SheetDescription>
      </SheetHeader>

      <div class="space-y-6 px-4 pb-6">
        <section class="space-y-2">
          <h3 id="appearance-mode" class="text-sm leading-none font-medium">Mode</h3>
          <RadioGroupRoot
            :model-value="colorMode.preference"
            aria-labelledby="appearance-mode"
            class="grid grid-cols-3 gap-2"
            @update:model-value="setMode"
          >
            <RadioGroupItem v-for="m in modes" :key="m.value" :value="m.value" as-child>
              <Button variant="outline" size="sm" :class="selected">
                <component :is="m.icon" />
                {{ m.label }}
              </Button>
            </RadioGroupItem>
          </RadioGroupRoot>
        </section>

        <section class="space-y-2">
          <h3 id="appearance-accent" class="text-sm leading-none font-medium">Accent</h3>
          <RadioGroupRoot
            :model-value="preset"
            aria-labelledby="appearance-accent"
            class="grid grid-cols-2 gap-2"
            @update:model-value="setPreset"
          >
            <RadioGroupItem :value="DEFAULT" as-child>
              <Button variant="outline" size="sm" class="justify-start" :class="selected">
                <span class="size-3.5 rounded-full border bg-foreground" />
                Default
                <CheckIcon v-if="preset === DEFAULT" class="ml-auto" />
              </Button>
            </RadioGroupItem>
            <RadioGroupItem v-for="p in PRESETS" :key="p.id" :value="p.id" as-child>
              <Button variant="outline" size="sm" class="justify-start" :class="selected">
                <span class="size-3.5 rounded-full" :style="{ background: p.swatch }" />
                {{ p.label }}
                <CheckIcon v-if="preset === p.id" class="ml-auto" />
              </Button>
            </RadioGroupItem>
          </RadioGroupRoot>
        </section>

        <section class="space-y-2">
          <h3 id="appearance-radius" class="text-sm leading-none font-medium">Corner radius</h3>
          <RadioGroupRoot
            :model-value="radius"
            aria-labelledby="appearance-radius"
            class="grid grid-cols-4 gap-2"
            @update:model-value="setRadius"
          >
            <RadioGroupItem :value="DEFAULT" as-child>
              <Button variant="outline" size="sm" :class="selected">Default</Button>
            </RadioGroupItem>
            <RadioGroupItem v-for="r in RADII" :key="r" :value="String(r)" as-child>
              <Button variant="outline" size="sm" :class="selected">{{ RADIUS_LABELS[r] }}</Button>
            </RadioGroupItem>
          </RadioGroupRoot>
        </section>
      </div>
    </SheetContent>
  </Sheet>
</template>
