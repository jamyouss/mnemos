import type { Config } from 'tailwindcss'

export default <Partial<Config>>{
  darkMode: 'class',
  content: ['./app.vue', './pages/**/*.vue', './components/**/*.vue', './layouts/**/*.vue'],
  theme: {
    extend: {
      colors: {
        background: 'hsl(var(--background))',
        foreground: 'hsl(var(--foreground))',
        card: 'hsl(var(--card))',
        muted: { DEFAULT: 'hsl(var(--muted))', foreground: 'hsl(var(--muted-foreground))' },
        border: 'hsl(var(--border))',
        primary: { DEFAULT: 'hsl(var(--primary))', foreground: 'hsl(var(--primary-foreground))' },
        destructive: 'hsl(var(--destructive))',
        success: 'hsl(var(--success))',
        warning: 'hsl(var(--warning))',
      },
      borderRadius: { lg: 'var(--radius)', md: 'calc(var(--radius) - 2px)' },
    },
  },
}
