<script setup lang="ts">
import { nextTick, onMounted, reactive, ref, watch } from 'vue'
import { changePassword, createUser, downloadAsrModel, errorMessage, getAsrSettings, listBatch, listAsrModels, listUsers, putAsrSettings, putBatchOptions, type AsrModel, type AsrService, type BatchOptions, type ManagedUser, patchUser, user } from '../api'
import { locale, t } from '../prefs'
import { CONTEXT_PRESETS, DEFAULT_TRANSCRIBE } from '../transcribe'

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
const configSources = reactive<Record<AsrService, 'auto' | 'personal' | 'shared'>>({ asr: 'auto', sentence: 'auto', translate: 'auto' })
const forms = reactive<Record<AsrService, Record<string, string>>>({ asr: {}, sentence: {}, translate: {} })
const saved = ref('')
const models = ref<AsrModel[]>([])
let modelsTimer: number | undefined
const transcribe = reactive<BatchOptions>({ ...DEFAULT_TRANSCRIBE })
const transcribeMsg = ref('')
const contextPreset = ref('131072')
const customContextWindow = ref('')
const contextRatio = ref(80)
let transcribeLoaded = false
let transcribeSaveTimer: number | undefined
let transcribeSavedTimer: number | undefined

const EDITABLE_FIELDS = ['kind', 'base_url', 'model', 'api_flavor', 'auth_style', 'aligner']

const formatDate = (value: string | null) =>
  value ? new Date(value).toLocaleString(locale.value === 'en' ? 'en' : 'zh-CN', { dateStyle: 'short', timeStyle: 'short' }) : '—'

function fillForms() {
  for (const service of ['asr', 'sentence', 'translate'] as AsrService[]) {
    const view = settings.value?.[service]
    const source = editShared.value && user.value?.role === 'admin' ? view?.shared : (view?.own ?? view?.shared)
    forms[service] = Object.fromEntries(EDITABLE_FIELDS.map((field) => [field, source?.[field] ?? '']))
  }
}

async function loadSettings() {
  try {
    settings.value = await getAsrSettings()
    for (const service of ['asr', 'sentence', 'translate'] as AsrService[]) {
      const source = settings.value[service].own?.config_source
      configSources[service] = source === 'shared' || source === 'personal' ? source : 'auto'
    }
    fillForms()
  } catch {
    error.value = t.value.settingsLoadFailed
  }
}

async function save(service: AsrService) {
  saved.value = ''
  const values = Object.fromEntries(Object.entries(forms[service]).filter(([, value]) => value !== ''))
  if (service === 'asr') values.kind = forms.asr.kind ?? ''
  values.config_source = configSources[service]
  const scope = editShared.value && user.value?.role === 'admin' ? 'shared' : 'user'
  try {
    await putAsrSettings(service, scope, values)
    saved.value = t.value.saveOk
    await loadSettings()
  } catch (err) {
    error.value = errorMessage(err)
  }
}

async function changeConfigSource(service: AsrService) {
  try {
    await putAsrSettings(service, 'user', { config_source: configSources[service] })
    saved.value = t.value.saveOk
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

async function loadTranscribe() {
  try {
    Object.assign(transcribe, (await listBatch()).options)
    const preset = String(transcribe.context_window_tokens)
    contextPreset.value = CONTEXT_PRESETS.includes(preset) ? preset : 'custom'
    customContextWindow.value = contextPreset.value === 'custom' ? preset : ''
    contextRatio.value = Math.round(Number(transcribe.max_context_ratio) * 100)
    await nextTick()
    transcribeLoaded = true
  } catch {
    error.value = t.value.settingsLoadFailed
  }
}

function setTranscribeMode(event: Event) {
  transcribe.mode = (event.target as HTMLInputElement).checked ? 'reflect' : 'standard'
}

function scheduleSaveTranscribe() {
  if (!transcribeLoaded) return
  if (contextPreset.value === 'custom' && (!Number.isInteger(Number(customContextWindow.value)) || Number(customContextWindow.value) < 1)) {
    error.value = t.value.invalidContextWindow
    return
  }
  window.clearTimeout(transcribeSaveTimer)
  transcribeSaveTimer = window.setTimeout(async () => {
    transcribe.context_window_tokens = contextPreset.value === 'custom' ? customContextWindow.value : contextPreset.value
    transcribe.max_context_ratio = String(Math.min(95, Math.max(10, Math.round(contextRatio.value))) / 100)
    try {
      await putBatchOptions({
        language: transcribe.language,
        target_language: transcribe.target_language,
        mode: transcribe.mode,
        reasoning_effort: transcribe.reasoning_effort,
        context_window_tokens: transcribe.context_window_tokens,
        max_context_ratio: transcribe.max_context_ratio,
      })
      transcribeMsg.value = t.value.saveOk
      window.clearTimeout(transcribeSavedTimer)
      transcribeSavedTimer = window.setTimeout(() => (transcribeMsg.value = ''), 2000)
    } catch (err) {
      error.value = errorMessage(err)
    }
  }, 400)
}

watch(
  () => [transcribe.language, transcribe.target_language, transcribe.mode, transcribe.reasoning_effort, contextPreset.value, customContextWindow.value, contextRatio.value],
  () => scheduleSaveTranscribe(),
)

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
  void loadTranscribe()
})
</script>

<template>
  <div class="flex flex-col gap-6">
    <h1 class="text-xl">{{ t.settings }}</h1>
    <p v-if="error" class="text-error">{{ error }}</p>

    <section class="flex flex-col gap-3">
      <div class="flex items-center gap-3">
        <h2 class="text-lg">{{ t.transcribeDefaults }}</h2>
        <span v-if="transcribeMsg" class="text-sm text-success">{{ transcribeMsg }}</span>
      </div>
      <div class="space-y-3 rounded-box border border-base-content/30 p-4">
        <fieldset class="fieldset">
          <label class="label p-0 text-sm font-semibold" for="default-language">{{ t.asrLanguage }}</label>
          <input id="default-language" v-model="transcribe.language" class="input w-full bg-base-200" />
          <p class="whitespace-pre-line text-xs text-base-content/60">{{ t.asrLanguageHint }}</p>
        </fieldset>
        <div class="grid gap-3 sm:grid-cols-2">
          <fieldset class="fieldset">
            <label class="label p-0 text-sm font-semibold" for="default-target">{{ t.targetLanguage }}</label>
            <input id="default-target" v-model="transcribe.target_language" class="input w-full bg-base-200" />
          </fieldset>
          <fieldset class="fieldset">
            <label class="label p-0 text-sm font-semibold" for="default-reasoning">{{ t.reasoningEffort }}</label>
            <select id="default-reasoning" v-model="transcribe.reasoning_effort" class="select w-full bg-base-200">
              <option value="off">{{ t.reasoningOff }}</option>
              <option value="low">{{ t.reasoningLow }}</option>
              <option value="medium">{{ t.reasoningMedium }}</option>
              <option value="high">{{ t.reasoningHigh }}</option>
            </select>
          </fieldset>
          <fieldset class="fieldset">
            <label class="label p-0 text-sm font-semibold" for="default-context">{{ t.contextWindow }}</label>
            <select id="default-context" v-model="contextPreset" class="select w-full bg-base-200">
              <option v-for="preset in CONTEXT_PRESETS" :key="preset" :value="preset">{{ Number(preset) / 1024 }}K</option>
              <option value="custom">{{ t.custom }}</option>
            </select>
            <input v-if="contextPreset === 'custom'" v-model="customContextWindow" class="input input-sm mt-1 w-full bg-base-200" type="number" min="1" :placeholder="t.customContextHint" />
          </fieldset>
          <fieldset class="fieldset">
            <label class="label p-0 text-sm font-semibold" for="default-ratio">{{ t.contextRatio }}</label>
            <label class="input w-full bg-base-200">
              <input id="default-ratio" v-model.number="contextRatio" class="grow" type="number" min="10" max="95" step="1" />
              <span class="text-base-content/60" aria-hidden="true">%</span>
            </label>
          </fieldset>
        </div>
        <label class="label cursor-pointer justify-start gap-3 p-0">
          <input class="toggle toggle-primary" type="checkbox" :checked="transcribe.mode === 'reflect'" @change="setTranscribeMode" />
          <span>{{ transcribe.mode === 'reflect' ? t.reflectMode : t.standardMode }}</span>
        </label>
      </div>
    </section>

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
        <h2 class="text-lg">{{ t.asrServiceTitle }} · {{ t.sentenceServiceTitle }} · {{ t.translateServiceTitle }}</h2>
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
          <fieldset v-if="user?.role !== 'admin'" class="fieldset">
            <label class="label p-0 text-sm font-semibold" for="asr-source">{{ t.configSource }}</label>
            <select id="asr-source" v-model="configSources.asr" class="select w-full bg-base-200" @change="changeConfigSource('asr')">
              <option value="auto">{{ t.automaticConfig }}</option>
              <option value="personal">{{ t.personalConfig }}</option>
              <option value="shared" :disabled="!settings?.asr.has_shared">{{ t.adminSharedConfig }}</option>
            </select>
          </fieldset>
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
                <option value="">{{ t.none }}</option>
                <option v-for="model in models.filter((m) => m.kind === 'asr' && m.installed)" :key="model.id" :value="model.id">
                  {{ model.name }}
                </option>
              </select>
            </fieldset>
            <fieldset class="fieldset">
              <label class="label p-0 text-sm font-semibold" for="asr-aligner">{{ t.aligner }}</label>
              <select id="asr-aligner" v-model="forms.asr.aligner" class="select w-full bg-base-200">
                <option value="">{{ t.none }}</option>
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
          <fieldset v-if="user?.role !== 'admin'" class="fieldset">
            <label class="label p-0 text-sm font-semibold" for="sentence-source">{{ t.configSource }}</label>
            <select id="sentence-source" v-model="configSources.sentence" class="select w-full bg-base-200" @change="changeConfigSource('sentence')">
              <option value="auto">{{ t.automaticConfig }}</option>
              <option value="personal">{{ t.personalConfig }}</option>
              <option value="shared" :disabled="!settings?.sentence.has_shared">{{ t.adminSharedConfig }}</option>
            </select>
          </fieldset>
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

        <form class="space-y-3 rounded-box border border-base-content/30 p-4" @submit.prevent="save('translate')">
          <h3 class="font-semibold">{{ t.translateServiceTitle }}</h3>
          <fieldset v-if="user?.role !== 'admin'" class="fieldset">
            <label class="label p-0 text-sm font-semibold" for="translate-source">{{ t.configSource }}</label>
            <select id="translate-source" v-model="configSources.translate" class="select w-full bg-base-200" @change="changeConfigSource('translate')">
              <option value="auto">{{ t.automaticConfig }}</option>
              <option value="personal">{{ t.personalConfig }}</option>
              <option value="shared" :disabled="!settings?.translate.has_shared">{{ t.adminSharedConfig }}</option>
            </select>
          </fieldset>
          <fieldset class="fieldset">
            <label class="label p-0 text-sm font-semibold" for="translate-base-url">{{ t.baseUrl }}</label>
            <input id="translate-base-url" v-model="forms.translate.base_url" class="input w-full bg-base-200" type="url" placeholder="https://api.openai.com/v1" />
          </fieldset>
          <fieldset class="fieldset">
            <label class="label p-0 text-sm font-semibold" for="translate-api-key">{{ t.apiKey }}</label>
            <input id="translate-api-key" v-model="forms.translate.api_key" class="input w-full bg-base-200" type="password" :placeholder="t.apiKeyHint" />
          </fieldset>
          <fieldset class="fieldset">
            <label class="label p-0 text-sm font-semibold" for="translate-model">{{ t.model }}</label>
            <input id="translate-model" v-model="forms.translate.model" class="input w-full bg-base-200" />
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
              <td>{{ formatDate(row.created_at) }}</td>
              <td>{{ formatDate(row.last_login_at) }}</td>
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
