<script setup lang="ts">
import { ref } from 'vue'
import { RouterLink, RouterView, useRouter } from 'vue-router'
import { logout, user } from '../api'
import { currentTheme, locale, setLocale, setTheme, t } from '../prefs'

const router = useRouter()
const dark = ref(currentTheme() === 'dark')

function onTheme(event: Event) {
  const on = (event.target as HTMLInputElement).checked
  dark.value = on
  setTheme(on ? 'dark' : 'light')
}

function onLocale(event: Event) {
  const value = (event.target as HTMLSelectElement).value
  setLocale(value === 'en' ? 'en' : 'zh')
}

async function onLogout() {
  await logout()
  await router.replace('/login')
}
</script>

<template>
  <div class="drawer lg:drawer-open min-h-screen">
    <input id="app-drawer" type="checkbox" class="drawer-toggle" />
    <div class="drawer-content flex flex-col">
      <div class="navbar bg-base-200">
        <div class="navbar-start">
          <label for="app-drawer" class="btn btn-ghost lg:hidden">{{ t.menu }}</label>
        </div>
        <div class="navbar-end gap-2">
          <label class="swap">
            <input type="checkbox" class="theme-controller" value="dark" :checked="dark" @change="onTheme" />
            <span class="swap-on">{{ t.dark }}</span>
            <span class="swap-off">{{ t.light }}</span>
          </label>
          <select class="select" :value="locale" @change="onLocale">
            <option value="zh">中文</option>
            <option value="en">English</option>
          </select>
          <span>{{ user?.username }}</span>
          <button class="btn btn-ghost" type="button" @click="onLogout">{{ t.logout }}</button>
        </div>
      </div>
      <main class="p-4">
        <RouterView />
      </main>
    </div>
    <div class="drawer-side">
      <label for="app-drawer" class="drawer-overlay" aria-label="close sidebar"></label>
      <ul class="menu bg-base-200 min-h-full w-80 p-4">
        <li class="menu-title">{{ t.appName }}</li>
        <li><RouterLink to="/">{{ t.home }}</RouterLink></li>
        <li><a class="menu-disabled">{{ t.tasks }} · {{ t.tasksSoon }}</a></li>
        <li><RouterLink to="/settings">{{ t.settings }}</RouterLink></li>
      </ul>
    </div>
  </div>
</template>
