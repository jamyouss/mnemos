export default defineNuxtConfig({
  compatibilityDate: '2026-01-01',

  // A local admin dashboard needs no SSR, and `nuxt generate` gives static
  // files the mnemos server can hand out itself — one port, no CORS, no extra
  // container.
  ssr: false,

  modules: ['@nuxtjs/tailwindcss'],
  css: ['~/assets/css/main.css'],

  // In dev the SPA runs on :3000 and the API on :8100. Proxying keeps the
  // browser on one origin, so no CORS middleware is needed on the server.
  nitro: {
    devProxy: {
      '/api': { target: 'http://localhost:8100/api', changeOrigin: true },
    },
  },

  devtools: { enabled: false },
  telemetry: false,
})
