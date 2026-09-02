<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { changePassword, createUser, downloadAsrModel, errorMessage, getAsrSettings, listAsrModels, listUsers, putAsrSettings, type AsrModel, type AsrService, type ManagedUser, patchUser, user } from '../api'
import { t } from '../prefs'

const currentPassword = ref('')
const newPassword = ref('')
const passwordMsg = ref('')
const error = ref('')
const users = ref<ManagedUser[]>([])
const createDialog = ref<HTMLDialogElement | null>(null)
const newUsername = ref('')
const newUserPassword = ref('')
const newRole = ref<'user' | 'admin'>('user')

const settings = ref<Record<AsrService, { own: Record<string, string> | null; shared: Record<string, string> | null; has_shared: boolean }> | null>(null)
const editShared = ref(false)
const forms = reactive<Record<AsrService, Record<string, string>>>({ asr: {}, sentence: {} })
const saved = ref('')
const models = ref<AsrModel[]>([])
let modelsTimer: number | undefined

function fillForms() {
  for (const service of ['asr', 'sentence'] as AsrService[]) {
    const view = settings.value?.[service]
    const source = editShared.value && user.value?.role === 'admin' ? view?.shared : (view?.own ?? view?.shared)
    forms[service] = { ...(source ?? {}) }
    forms[service].api_key = ''
  }
}

async function loadSettings() {
  try {
    settings.value = await getAsrSettings()
    fillForms()
  } catch {
    error.value = t.value.settingsLoadFailed
  }
}

async function save(service: AsrService) {
  saved.value = ''
  const values = Object.fromEntries(Object.entries(forms[service]).filter(([, value]) => value !== ''))
  if (service === 'asr') values.kind = forms.asr.kind ?? ''
  const scope = editShared.value && user.value?.role === 'admin' ? 'shared' : 'user'
  try {
    await putAsrSettings(service, scope, values)
    saved.value = t.value.saveOk
    await loadSettings()
  } catch (err) {
    error.value = errorMessage(err)
  }
}

async function loadModels() {
  if (user.value?.role !== 'admin') return
  models.value = await listAsrModels()
  const downloading = models.value.some((model) => model.download?.status === 'downloading')
  window.clearInterval(modelsTimer)
  if (downloading) modelsTimer = window.setTimeout(loadModels, 1500)
}

async function startDownload(id: string) {
  error.value = ''
  try {
    await downloadAsrModel(id)
    await loadModels()
  } catch (err) {
    error.value = errorMessage(err)
  }
}

function downloadText(model: AsrModel) {
  if (model.installed) return t.value.modelDone
  const state = model.download
  if (state?.status === 'error') return `${t.value.modelError}: ${state.error ?? ''}`
  if (state?.status === 'downloading') {
    return state.total
      ? `${t.value.modelDownloading} ${Math.round((state.received / state.total) * 100)}%`
      : t.value.modelDownloading
  }
  return ''
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
    error.value = errorMessage(err)
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
    error.value = errorMessage(err)
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
    error.value = errorMessage(err)
  }
}

onMounted(() => {
  void loadSettings()
  void loadModels()
  void loadUsers()
})
</script>

<template>
  <div class="flex flex-col gap-6">
    <h1 class="text-xl">{{ t.settings }}</h1>
    <p v-if="error" class="text-error">{{ error }}</p>

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

    <section class="flex flex-col gap-3">
      <div class="flex flex-wrap items-center gap-3">
        <h2 class="text-lg">{{ t.asrServiceTitle }} · {{ t.sentenceServiceTitle }}</h2>
        <label v-if="user?.role === 'admin'" class="label cursor-pointer gap-2">
          <input class="toggle" type="checkbox" v-model="editShared" @change="fillForms" />
          <span class="text-sm">{{ t.sharedConfig }}</span>
        </label>
        <span v-if="saved" class="text-success text-sm">{{ saved }}</span>
      </div>
      <p v-if="user?.role !== 'admin'" class="text-xs text-base-content/60">{{ t.sharedHint }}</p>

      <div class="grid gap-4 lg:grid-cols-2">
        <form class="space-y-3 rounded-box border border-base-content/30 p-4" @submit.prevent="save('asr')">
          <h3 class="font-semibold">{{ t.asrServiceTitle }}</h3>
          <fieldset class="fieldset">
            <label class="label p-0 text-sm font-semibold" for="asr-kind">{{ t.serviceKind }}</label>
            <select id="asr-kind" v-model="forms.asr.kind" class="select w-full bg-base-200">
              <option value="">{{ t.kindNone }}</option>
              <option value="cloud">{{ t.kindCloud }}</option>
              <option value="local">{{ t.kindLocal }}</option>
            </select>
          </fieldset>
          <template v-if="forms.asr.kind === 'cloud'">
            <fieldset class="fieldset">
              <label class="label p-0 text-sm font-semibold" for="asr-base-url">{{ t.baseUrl }}</label>
              <input id="asr-base-url" v-model="forms.asr.base_url" class="input w-full bg-base-200" type="url" placeholder="https://api.openai.com/v1" />
            </fieldset>
            <fieldset class="fieldset">
              <label class="label p-0 text-sm font-semibold" for="asr-api-key">{{ t.apiKey }}</label>
              <input id="asr-api-key" v-model="forms.asr.api_key" class="input w-full bg-base-200" type="password" :placeholder="t.apiKeyHint" />
            </fieldset>
            <fieldset class="fieldset">
              <label class="label p-0 text-sm font-semibold" for="asr-model">{{ t.model }}</label>
              <input id="asr-model" v-model="forms.asr.model" class="input w-full bg-base-200" placeholder="whisper-1 / voxtral-mini-latest" />
            </fieldset>
            <div class="grid gap-3 sm:grid-cols-2">
              <fieldset class="fieldset">
                <label class="label p-0 text-sm font-semibold" for="asr-flavor">{{ t.apiFlavor }}</label>
                <select id="asr-flavor" v-model="forms.asr.api_flavor" class="select w-full bg-base-200">
                  <option value="openai">OpenAI</option>
                  <option value="mistral">Mistral</option>
                </select>
              </fieldset>
              <fieldset class="fieldset">
                <label class="label p-0 text-sm font-semibold" for="asr-auth">{{ t.authStyle }}</label>
                <select id="asr-auth" v-model="forms.asr.auth_style" class="select w-full bg-base-200">
                  <option value="">{{ t.authBearer }}</option>
                  <option value="x-api-key">{{ t.authXApiKey }}</option>
                </select>
              </fieldset>
            </div>
          </template>
          <template v-else-if="forms.asr.kind === 'local'">
            <fieldset class="fieldset">
              <label class="label p-0 text-sm font-semibold" for="asr-model-select">{{ t.model }}</label>
              <select id="asr-model-select" v-model="forms.asr.model" class="select w-full bg-base-200">
                <option value=""></option>
                <option v-for="model in models.filter((m) => m.kind === 'asr' && m.installed)" :key="model.id" :value="model.id">
                  {{ model.name }}
                </option>
              </select>
            </fieldset>
            <fieldset class="fieldset">
              <label class="label p-0 text-sm font-semibold" for="asr-aligner">{{ t.aligner }}</label>
              <select id="asr-aligner" v-model="forms.asr.aligner" class="select w-full bg-base-200">
                <option value=""></option>
                <option v-for="model in models.filter((m) => m.kind === 'aligner' && m.installed)" :key="model.id" :value="model.id">
                  {{ model.name }}
                </option>
              </select>
            </fieldset>
          </template>
          <button class="btn btn-primary" type="submit">{{ t.save }}</button>
        </form>

        <form class="space-y-3 rounded-box border border-base-content/30 p-4" @submit.prevent="save('sentence')">
          <h3 class="font-semibold">{{ t.sentenceServiceTitle }}</h3>
          <fieldset class="fieldset">
            <label class="label p-0 text-sm font-semibold" for="sentence-base-url">{{ t.baseUrl }}</label>
            <input id="sentence-base-url" v-model="forms.sentence.base_url" class="input w-full bg-base-200" type="url" placeholder="https://api.openai.com/v1" />
          </fieldset>
          <fieldset class="fieldset">
            <label class="label p-0 text-sm font-semibold" for="sentence-api-key">{{ t.apiKey }}</label>
            <input id="sentence-api-key" v-model="forms.sentence.api_key" class="input w-full bg-base-200" type="password" :placeholder="t.apiKeyHint" />
          </fieldset>
          <fieldset class="fieldset">
            <label class="label p-0 text-sm font-semibold" for="sentence-model">{{ t.model }}</label>
            <input id="sentence-model" v-model="forms.sentence.model" class="input w-full bg-base-200" />
          </fieldset>
          <button class="btn btn-primary" type="submit">{{ t.save }}</button>
        </form>
      </div>
    </section>

    <section v-if="user?.role === 'admin'" class="flex flex-col gap-3">
      <h2 class="text-lg">{{ t.modelManage }}</h2>
      <ul class="flex flex-col gap-2">
        <li v-for="model in models" :key="model.id" class="flex items-center gap-3 rounded-field bg-base-200 p-3">
          <div class="min-w-0 flex-1">
            <p class="truncate text-sm font-semibold">{{ model.name }}</p>
            <p class="text-xs text-base-content/60">{{ model.kind }} · {{ model.filename }}</p>
          </div>
          <span
            v-if="downloadText(model)"
            class="badge badge-sm shrink-0"
            :class="model.installed ? 'badge-success' : model.download?.status === 'error' ? 'badge-error' : 'badge-ghost'"
          >
            {{ downloadText(model) }}
          </span>
          <button v-if="!model.installed" class="btn btn-sm shrink-0" type="button" @click="startDownload(model.id)">
            {{ t.downloadModel }}
          </button>
        </li>
      </ul>
    </section>

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
