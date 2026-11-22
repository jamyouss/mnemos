import tailwindcss from '@tailwindcss/vite'

export default defineNuxtConfig({
  compatibilityDate: '2026-01-01',

  // A local admin dashboard needs no SSR, and `nuxt generate` gives static
  // files the mnemos server can hand out itself — one port, no CORS, no extra
  // container.
  ssr: false,

  modules: ['shadcn-nuxt', '@nuxtjs/color-mode'],
  css: ['~/assets/css/main.css'],

  shadcn: { prefix: '', componentDir: './components/ui' },

  // `.dark` on <html>, which is what the shadcn tokens key on.
  colorMode: { classSuffix: '', preference: 'system', fallback: 'light', storageKey: 'mnemos.color-mode' },

  app: {
    head: {
      // Served by the mnemos server from config/theme.css. Loaded after the
      // bundled styles so an instance's tokens win over the defaults.
      link: [{ rel: 'stylesheet', href: '/theme.css' }],
    },
  },

  vite: { plugins: [tailwindcss()] },

  // In dev the SPA runs on :3000 and the API on :8100. Proxying keeps the
  // browser on one origin, so no CORS middleware is needed on the server.
  nitro: {
    devProxy: {
      '/api': { target: 'http://localhost:8100/api', changeOrigin: true },
      '/theme.css': { target: 'http://localhost:8100/theme.css', changeOrigin: true },
    },
  },

  devtools: { enabled: false },
  telemetry: false,
})
