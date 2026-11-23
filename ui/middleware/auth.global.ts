import { sessionReady } from '~/composables/useSession'

export default defineNuxtRouteMiddleware(async (to) => {
  if (to.meta.public) return
  if (!(await sessionReady())) {
    return navigateTo({ path: '/login', query: { redirect: to.fullPath } })
  }
})
