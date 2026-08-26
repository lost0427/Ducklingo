import { computed, ref } from 'vue'
import en from './locales/en'
import zh from './locales/zh'

export const locale = ref<'zh' | 'en'>(localStorage.getItem('locale') === 'en' ? 'en' : 'zh')
export const t = computed(() => (locale.value === 'en' ? en : zh))

export function setLocale(next: 'zh' | 'en') {
  locale.value = next
  localStorage.setItem('locale', next)
}

export function currentTheme(): 'light' | 'dark' {
  const stored = localStorage.getItem('theme')
  if (stored === 'dark' || stored === 'light') return stored
  return matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export function setTheme(theme: 'light' | 'dark') {
  document.documentElement.setAttribute('data-theme', theme)
  localStorage.setItem('theme', theme)
}
