<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { login } from '../api'
import { t } from '../prefs'

const router = useRouter()
const username = ref('')
const password = ref('')
const error = ref('')

async function submit() {
  error.value = ''
  try {
    await login(username.value, password.value)
    await router.replace('/')
  } catch {
    error.value = t.value.loginFailed
  }
}
</script>

<template>
  <main class="grid min-h-screen place-items-center p-4">
    <div class="card card-border w-full max-w-sm">
      <form class="card-body" @submit.prevent="submit">
        <h1 class="card-title">{{ t.appName }}</h1>
        <fieldset class="fieldset">
          <legend class="fieldset-legend">{{ t.login }}</legend>
          <label class="label" for="username">{{ t.username }}</label>
          <input id="username" v-model="username" class="input w-full" required />
          <label class="label" for="password">{{ t.password }}</label>
          <input id="password" v-model="password" class="input w-full" type="password" required />
        </fieldset>
        <p v-if="error" class="text-error">{{ error }}</p>
        <div class="card-actions">
          <button class="btn btn-primary" type="submit">{{ t.login }}</button>
        </div>
      </form>
    </div>
  </main>
</template>
