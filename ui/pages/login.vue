<script setup lang="ts">
import { EyeIcon, EyeOffIcon, Loader2Icon } from '@lucide/vue'
import { signIn } from '~/composables/useSession'

definePageMeta({ title: 'Sign in', public: true, bare: true })
useHead({ title: 'Sign in' })

const route = useRoute()
const key = ref('')
const reveal = ref(false)
const busy = ref(false)
const error = ref('')

// Only paths inside the dashboard: a crafted ?redirect= must not leave the site.
const target = computed(() => {
  const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/'
  return redirect.startsWith('/') && !redirect.startsWith('//') ? redirect : '/'
})

async function submit() {
  busy.value = true
  error.value = ''
  const result = await signIn(key.value.trim())
  busy.value = false
  if (result === 'ok') return navigateTo(target.value, { replace: true })
  error.value = result === 'invalid'
    ? 'This key is not valid, or it was revoked.'
    : 'The server cannot be reached. Try again in a moment.'
}
</script>

<template>
  <div class="flex min-h-svh items-center justify-center bg-muted/40 p-6">
    <div class="w-full max-w-sm space-y-6">
      <div class="flex flex-col items-center gap-3 text-center">
        <img src="/logo.svg" alt="" class="size-12">
        <div class="space-y-1">
          <h1 class="text-xl font-semibold tracking-tight">Sign in to mnemos</h1>
          <p class="text-sm text-muted-foreground">Enter your mnemos API key.</p>
        </div>
      </div>

      <Card>
        <CardContent>
          <form class="space-y-4" @submit.prevent="submit">
            <div class="space-y-2">
              <Label for="api-key">API key</Label>
              <div class="relative">
                <Input
                  id="api-key"
                  v-model="key"
                  :type="reveal ? 'text' : 'password'"
                  autocomplete="current-password"
                  autofocus
                  class="pr-10 font-mono"
                  :aria-invalid="!!error"
                  :aria-describedby="error ? 'api-key-error' : undefined"
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  class="absolute top-1/2 right-1 size-7 -translate-y-1/2"
                  :aria-label="reveal ? 'Hide the key' : 'Show the key'"
                  @click="reveal = !reveal"
                >
                  <EyeOffIcon v-if="reveal" />
                  <EyeIcon v-else />
                </Button>
              </div>
              <p v-if="error" id="api-key-error" class="text-sm text-destructive" role="alert">{{ error }}</p>
            </div>
            <Button type="submit" class="w-full" :disabled="busy || !key.trim()">
              <Loader2Icon v-if="busy" class="animate-spin" />
              Sign in
            </Button>
          </form>
        </CardContent>
      </Card>

      <p class="text-center text-xs text-muted-foreground">
        An admin issues keys from the Tenants page. The key stays in this browser.
      </p>
    </div>
  </div>
</template>
