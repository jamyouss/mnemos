<script setup lang="ts">
import { InfoIcon } from '@lucide/vue'
import type { Whoami } from '~/utils/tenants'

/**
 * Why a configuration screen cannot be edited, when it cannot; nothing otherwise.
 * Editing is switched by MNEMOS_UI_CONFIG_WRITABLE on the server.
 */
defineProps<{ writable: boolean; me: Whoami | null | undefined }>()
</script>

<template>
  <Alert v-if="me && !me.admin">
    <InfoIcon />
    <AlertTitle>Read-only</AlertTitle>
    <AlertDescription>You can view this page but only an admin can change it.</AlertDescription>
  </Alert>
  <Alert v-else-if="!writable">
    <InfoIcon />
    <AlertTitle>Read-only</AlertTitle>
    <AlertDescription>Editing is turned off on this server. Ask the server administrator to enable it.</AlertDescription>
  </Alert>
</template>
