<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { changePassword, createUser, listUsers, type ManagedUser, patchUser, user } from '../api'
import { locale, setLocale, t } from '../prefs'

const currentPassword = ref('')
const newPassword = ref('')
const passwordMsg = ref('')
const error = ref('')
const users = ref<ManagedUser[]>([])
const createDialog = ref<HTMLDialogElement | null>(null)
const newUsername = ref('')
const newUserPassword = ref('')
const newRole = ref<'user' | 'admin'>('user')

function detail(err: unknown) {
  const text = err instanceof Error ? err.message : String(err)
  try {
    const parsed = JSON.parse(text) as { detail?: string }
    return parsed.detail || text
  } catch {
    return text
  }
}

async function loadUsers() {
  if (user.value?.role !== 'admin') return
  users.value = await listUsers()
}

async function savePassword() {
  error.value = ''
  passwordMsg.value = ''
  try {
    await changePassword(currentPassword.value, newPassword.value)
    currentPassword.value = ''
    newPassword.value = ''
    passwordMsg.value = t.value.passwordChanged
  } catch (err) {
    error.value = detail(err)
  }
}

async function toggleActive(row: ManagedUser, event: Event) {
  const active = (event.target as HTMLInputElement).checked
  error.value = ''
  try {
    await patchUser(row.id, { is_active: active })
    await loadUsers()
  } catch (err) {
    ;(event.target as HTMLInputElement).checked = row.is_active
    error.value = detail(err)
  }
}

async function submitCreate() {
  error.value = ''
  try {
    await createUser(newUsername.value, newUserPassword.value, newRole.value)
    newUsername.value = ''
    newUserPassword.value = ''
    newRole.value = 'user'
    createDialog.value?.close()
    await loadUsers()
  } catch (err) {
    error.value = detail(err)
  }
}

function onLocale(event: Event) {
  setLocale((event.target as HTMLSelectElement).value === 'en' ? 'en' : 'zh')
}

onMounted(() => {
  void loadUsers()
})
</script>

<template>
  <div class="flex flex-col gap-6">
    <h1 class="text-xl">{{ t.settings }}</h1>
    <p v-if="error" class="text-error">{{ error }}</p>

    <fieldset class="fieldset max-w-sm">
      <legend class="fieldset-legend">{{ t.language }}</legend>
      <select class="select" :value="locale" @change="onLocale">
        <option value="zh">中文</option>
        <option value="en">English</option>
      </select>
    </fieldset>

    <form class="max-w-sm" @submit.prevent="savePassword">
      <fieldset class="fieldset">
        <legend class="fieldset-legend">{{ t.changePassword }}</legend>
        <label class="label" for="current-password">{{ t.currentPassword }}</label>
        <input id="current-password" v-model="currentPassword" class="input w-full" type="password" required />
        <label class="label" for="new-password">{{ t.newPassword }}</label>
        <input id="new-password" v-model="newPassword" class="input w-full" type="password" minlength="12" required />
      </fieldset>
      <p v-if="passwordMsg" class="text-success">{{ passwordMsg }}</p>
      <button class="btn btn-primary mt-2" type="submit">{{ t.save }}</button>
    </form>

    <section v-if="user?.role === 'admin'" class="flex flex-col gap-3">
      <div class="flex items-center gap-2">
        <h2 class="text-lg">{{ t.users }}</h2>
        <button class="btn" type="button" @click="createDialog?.showModal()">{{ t.createUser }}</button>
      </div>
      <div class="overflow-x-auto">
        <table class="table">
          <thead>
            <tr>
              <th>{{ t.username }}</th>
              <th>{{ t.role }}</th>
              <th>{{ t.active }}</th>
              <th>{{ t.created }}</th>
              <th>{{ t.lastLogin }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in users" :key="row.id">
              <td>{{ row.username }}</td>
              <td>{{ row.role === 'admin' ? t.roleAdmin : t.roleUser }}</td>
              <td>
                <input class="toggle" type="checkbox" :checked="row.is_active" @change="toggleActive(row, $event)" />
              </td>
              <td>{{ row.created_at }}</td>
              <td>{{ row.last_login_at }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <dialog ref="createDialog" class="modal">
      <form class="modal-box" @submit.prevent="submitCreate">
        <h3 class="text-lg">{{ t.createUser }}</h3>
        <fieldset class="fieldset">
          <label class="label" for="new-username">{{ t.username }}</label>
          <input id="new-username" v-model="newUsername" class="input w-full" required />
          <label class="label" for="new-user-password">{{ t.password }}</label>
          <input id="new-user-password" v-model="newUserPassword" class="input w-full" type="password" minlength="12" required />
          <label class="label" for="new-role">{{ t.role }}</label>
          <select id="new-role" v-model="newRole" class="select">
            <option value="user">{{ t.roleUser }}</option>
            <option value="admin">{{ t.roleAdmin }}</option>
          </select>
        </fieldset>
        <div class="modal-action">
          <button class="btn" type="button" @click="createDialog?.close()">{{ t.cancel }}</button>
          <button class="btn" type="submit">{{ t.createUser }}</button>
        </div>
      </form>
      <form method="dialog" class="modal-backdrop"><button>{{ t.cancel }}</button></form>
    </dialog>
  </div>
</template>
