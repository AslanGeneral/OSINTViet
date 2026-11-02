/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        'brand-dark': '#111827',
        'brand-slate': '#1e293b',
        'brand-accent': '#9B1C31', // Deep Crimson / Irregular Warfare Red
        'brand-accent-hover': '#801426',
        'brand-subtle': '#FFF5F7',
        'brand-ivory': '#FAFAFA',
        'brand-border': '#E5E7EB',
      },
      fontFamily: {
        serif: ['Merriweather', 'Georgia', 'serif'],
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'Menlo', 'monospace'],
      },
      boxShadow: {
        'editorial': '0 4px 20px -2px rgba(17, 24, 39, 0.05)',
        'drawer': '-10px 0 30px -5px rgba(0, 0, 0, 0.25)',
      }
    },
  },
  plugins: [],
}
