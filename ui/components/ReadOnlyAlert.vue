<script setup lang="ts">
import { InfoIcon } from '@lucide/vue'
import type { Whoami } from '~/utils/tenants'

/** Why a configuration screen cannot be edited, when it cannot; nothing otherwise. */
defineProps<{ writable: boolean; me: Whoami | null | undefined }>()
</script>

<template>
  <Alert v-if="me && !me.admin">
    <InfoIcon />
    <AlertTitle>Read-only</AlertTitle>
    <AlertDescription>
      Tenant “{{ me.name }}” writes every project, so it may read this; only an admin key can change it.
    </AlertDescription>
  </Alert>
  <Alert v-else-if="!writable">
    <InfoIcon />
    <AlertTitle>Read-only</AlertTitle>
    <AlertDescription>
      Editing is off on this server. Set <code>MNEMOS_UI_CONFIG_WRITABLE=true</code> to enable it — and turn on
      <code>MNEMOS_AUTH_ENABLED</code> first if anyone else can reach port 8100.
    </AlertDescription>
  </Alert>
</template>
