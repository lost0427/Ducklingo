<script setup lang="ts">
import { ref } from 'vue'
import { RouterLink, RouterView, useRouter } from 'vue-router'
import {
  ChevronRightIcon,
  Cog6ToothIcon,
  DocumentTextIcon,
  HomeIcon,
  MoonIcon,
  SunIcon,
} from '@heroicons/vue/24/outline'
import { logout, user } from '../api'
import { currentTheme, locale, setLocale, setTheme, t } from '../prefs'

const router = useRouter()
const dark = ref(currentTheme() === 'dark')

function onTheme(event: Event) {
  const on = (event.target as HTMLInputElement).checked
  dark.value = on
  setTheme(on ? 'dark' : 'light')
}

async function onLogout() {
  await logout()
  await router.replace('/login')
}
</script>

<template>
  <div class="drawer lg:drawer-open min-h-screen">
    <input id="app-drawer" type="checkbox" class="drawer-toggle" />
    <div class="drawer-content">
      <nav class="navbar bg-base-200">
        <div class="navbar-start">
          <label for="app-drawer" class="btn btn-ghost drawer-button lg:hidden">{{ t.menu }}</label>
        </div>
        <div class="navbar-end gap-2">
          <label class="swap swap-rotate btn btn-ghost btn-circle" :aria-label="dark ? t.light : t.dark">
            <input type="checkbox" class="theme-controller" value="dark" :checked="dark" @change="onTheme" />
            <MoonIcon class="swap-on size-6" aria-hidden="true" />
            <SunIcon class="swap-off size-6" aria-hidden="true" />
          </label>
          <div class="join">
            <button
              type="button"
              class="btn btn-sm join-item w-8"
              :class="locale === 'zh' ? 'btn-active' : ''"
              @click="setLocale('zh')"
            >
              中
            </button>
            <button
              type="button"
              class="btn btn-sm join-item w-8"
              :class="locale === 'en' ? 'btn-active' : ''"
              @click="setLocale('en')"
            >
              EN
            </button>
          </div>
          <span class="text-sm">{{ user?.username }}</span>
          <button class="btn btn-ghost btn-sm w-20" type="button" @click="onLogout">{{ t.logout }}</button>
        </div>
      </nav>
      <main class="p-4">
        <RouterView />
      </main>
    </div>
    <div class="drawer-side is-drawer-close:overflow-visible">
      <label for="app-drawer" class="drawer-overlay" aria-label="close sidebar"></label>
      <div class="flex min-h-full flex-col items-start bg-base-200 is-drawer-close:w-14 is-drawer-open:w-64">
        <div class="h-12 w-full flex items-center px-5 border-b border-base-300 overflow-hidden whitespace-nowrap">
          <span class="font-bold text-lg is-drawer-open:hidden">D</span>
          <span class="font-bold text-lg is-drawer-close:hidden">{{ t.appName }}</span>
        </div>
        <ul class="menu w-full grow">
          <li>
            <RouterLink to="/" class="is-drawer-close:tooltip is-drawer-close:tooltip-right" :data-tip="t.home">
              <HomeIcon class="my-1.5 inline-block size-4" aria-hidden="true" />
              <span class="is-drawer-close:hidden truncate">{{ t.home }}</span>
            </RouterLink>
          </li>
          <li>
            <RouterLink to="/tasks" class="is-drawer-close:tooltip is-drawer-close:tooltip-right" :data-tip="t.tasks">
              <DocumentTextIcon class="my-1.5 inline-block size-4" aria-hidden="true" />
              <span class="is-drawer-close:hidden truncate">{{ t.tasks }}</span>
            </RouterLink>
          </li>
          <li>
            <RouterLink to="/settings" class="is-drawer-close:tooltip is-drawer-close:tooltip-right" :data-tip="t.settings">
              <Cog6ToothIcon class="my-1.5 inline-block size-4" aria-hidden="true" />
              <span class="is-drawer-close:hidden truncate">{{ t.settings }}</span>
            </RouterLink>
          </li>
        </ul>
        <div class="m-2 is-drawer-close:tooltip is-drawer-close:tooltip-right" :data-tip="t.menu">
          <label for="app-drawer" class="btn btn-ghost btn-circle drawer-button">
            <ChevronRightIcon class="my-1.5 inline-block size-4 is-drawer-open:rotate-y-180" aria-hidden="true" />
          </label>
        </div>
      </div>
    </div>
  </div>
</template>
