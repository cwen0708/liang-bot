import { ref, computed, watch, onMounted } from 'vue'

type ThemeMode = 'light' | 'dark' | 'system'

const STORAGE_KEY = 'spot-bot-theme'

const mode = ref<ThemeMode>((localStorage.getItem(STORAGE_KEY) as ThemeMode) || 'dark')

const systemPrefersDark = ref(window.matchMedia('(prefers-color-scheme: dark)').matches)

const isDark = computed(() => {
  if (mode.value === 'system') return systemPrefersDark.value
  return mode.value === 'dark'
})

function applyTheme() {
  const html = document.documentElement
  if (isDark.value) {
    html.classList.add('dark')
  } else {
    html.classList.remove('dark')
  }

  const meta = document.querySelector('meta[name="theme-color"]')
  if (meta) {
    meta.setAttribute('content', isDark.value ? '#0c0f16' : '#f5f6fa')
  }
}

// Watch and persist
watch(mode, (val) => {
  localStorage.setItem(STORAGE_KEY, val)
  applyTheme()
})

watch(systemPrefersDark, () => {
  if (mode.value === 'system') applyTheme()
})

// Listen for system preference changes
const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)')
mediaQuery.addEventListener('change', (e) => {
  systemPrefersDark.value = e.matches
})

// Apply on load
applyTheme()

export function useTheme() {
  function toggleTheme() {
    mode.value = isDark.value ? 'light' : 'dark'
  }

  return {
    mode,
    isDark,
    toggleTheme,
  }
}

/** Read computed CSS variable values for lightweight-charts (which doesn't support var()) */
export function useChartColors() {
  function getCSSVar(name: string): string {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  }

  function getColors() {
    return {
      bg: getCSSVar('--color-chart-bg'),
      text: getCSSVar('--color-chart-text'),
      grid: getCSSVar('--color-chart-grid'),
      line: getCSSVar('--color-chart-line'),
      baselineTop: getCSSVar('--color-chart-baseline-top'),
      baselineTopFill1: getCSSVar('--color-chart-baseline-top-fill1'),
      baselineTopFill2: getCSSVar('--color-chart-baseline-top-fill2'),
      baselineBottom: getCSSVar('--color-chart-baseline-bottom'),
      baselineBottomFill1: getCSSVar('--color-chart-baseline-bottom-fill1'),
      baselineBottomFill2: getCSSVar('--color-chart-baseline-bottom-fill2'),
      pricelineLow: getCSSVar('--color-chart-priceline-low'),
      pricelineHigh: getCSSVar('--color-chart-priceline-high'),
    }
  }

  return { getColors, isDark }
}
