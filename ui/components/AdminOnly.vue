<script setup lang="ts">
import { KeyRoundIcon, ShieldIcon } from '@lucide/vue'
import { signOut } from '~/composables/useSession'
import type { Whoami } from '~/utils/tenants'

/** What a non-admin sees instead of a screen that only admins may read. */
const props = defineProps<{ me: Whoami; what: string }>()

const detail = computed(
  () => `This dashboard uses the API key of tenant “${props.me.name}”, which is not an admin. ${props.what} needs an admin key.`,
)
</script>

<template>
  <EmptyState :icon="ShieldIcon" title="Admins only" :detail="detail">
    <Button size="sm" variant="outline" @click="signOut"><KeyRoundIcon /> Use another key</Button>
  </EmptyState>
</template>
