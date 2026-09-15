<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import {
  addBatch,
  asrArchiveUrl,
  cancelBatch,
  deleteBatch,
  errorMessage,
  listBatch,
  putBatchOptions,
  retryBatch,
  type BatchItem,
  type BatchOptions,
} from '../api'
import { t } from '../prefs'
import { DEFAULT_TRANSCRIBE, transcribeSummary } from '../transcribe'

const URLS_KEY = 'batchUrls'
const urls = ref(localStorage.getItem(URLS_KEY) || '')
const items = ref<BatchItem[]>([])
const error = ref('')
const options = reactive<BatchOptions>({ ...DEFAULT_TRANSCRIBE })
const qualities = [
  { value: 'best', label: t.value.qualityBest },
  { value: '1080', label: t.value.quality1080 },
  { value: '720', label: t.value.quality720 },
  { value: '480', label: t.value.quality480 },
]
const running = computed(() => items.value.some((item) => !['done', 'error', 'cancelled'].includes(item.status)))
const transcribeText = computed(() => transcribeSummary(options, t.value))
const statusText = (status: string) =>
  ({
    pending: t.value.asrStageQueue,
    download: t.value.downloading,
    asr: t.value.asrStageTranscribe,
    sentence: t.value.asrStageSentence,
    translate: t.value.translationStage,
    done: t.value.asrDone,
    error: t.value.asrFailed,
    cancelled: t.value.asrCancelled,
  })[status] ?? status

const saved = ref(false)
let timer: number | undefined
let optionsLoaded = false
let saveTimer: number | undefined
let savedTimer: number | undefined

async function load() {
  try {
    const state = await listBatch()
    items.value = state.items
    if (!optionsLoaded) {
      Object.assign(options, state.options)
      optionsLoaded = true
    }
  } catch (err) {
    error.value = errorMessage(err)
  }
}

function scheduleSave() {
  if (!optionsLoaded) return
  window.clearTimeout(saveTimer)
  saveTimer = window.setTimeout(async () => {
    try {
      await putBatchOptions({ quality: options.quality })
      saved.value = true
      window.clearTimeout(savedTimer)
      savedTimer = window.setTimeout(() => (saved.value = false), 2000)
    } catch (err) {
      error.value = errorMessage(err)
    }
  }, 400)
}

async function add() {
  const list = urls.value.split('\n').map((line) => line.trim()).filter(Boolean)
  if (!list.length) {
    error.value = t.value.noValidUrls
    return
  }
  error.value = ''
  try {
    await putBatchOptions({ quality: options.quality })
    await addBatch(list)
    urls.value = ''
    await load()
  } catch (err) {
    error.value = errorMessage(err)
  }
}

async function act(action: (id: number) => Promise<unknown>, id: number) {
  error.value = ''
  try {
    await action(id)
    await load()
  } catch (err) {
    error.value = errorMessage(err)
  }
}

onMounted(() => {
  void load()
  timer = window.setInterval(() => {
    if (running.value) void load()
  }, 2000)
})
onUnmounted(() => window.clearInterval(timer))
watch(urls, (value) => localStorage.setItem(URLS_KEY, value))
watch(() => options.quality, () => scheduleSave())
</script>

<template>
  <section class="mx-auto max-w-3xl py-4 sm:py-8">
    <h1 class="mb-4 text-3xl font-bold tracking-tight sm:text-4xl">{{ t.tasksTitle }}</h1>
    <div class="space-y-4 rounded-box border border-base-content/30 p-3 sm:p-4">
      <fieldset class="fieldset">
        <label class="label p-0 text-sm font-semibold" for="batch-urls">{{ t.urlsLabel }}</label>
        <textarea id="batch-urls" v-model="urls" rows="5" class="textarea w-full bg-base-200 font-mono text-sm"></textarea>
      </fieldset>
      <fieldset class="fieldset">
        <label class="label p-0 text-sm font-semibold" for="batch-quality">{{ t.quality }}</label>
        <select id="batch-quality" v-model="options.quality" class="select w-full bg-base-200">
          <option v-for="option in qualities" :key="option.value" :value="option.value">{{ option.label }}</option>
        </select>
      </fieldset>
      <div class="flex flex-wrap items-center justify-between gap-2 rounded-field bg-base-200 p-3 text-sm">
        <span class="min-w-0 break-words">{{ transcribeText }}</span>
        <RouterLink to="/settings" class="link link-primary shrink-0">{{ t.transcribeEdit }}</RouterLink>
      </div>
      <button class="btn btn-primary w-full" type="button" @click="add">{{ t.addToQueue }}</button>
      <p v-if="saved" class="text-xs text-success">{{ t.batchSaved }}</p>
      <p v-if="error" role="alert" class="alert alert-error"><span class="break-all">{{ error }}</span></p>
    </div>

    <ul class="mt-4 space-y-2">
      <li v-if="!items.length" class="text-sm text-base-content/60">{{ t.queueEmpty }}</li>
      <li v-for="item in items" :key="item.id" class="flex items-start gap-3 rounded-box border border-base-content/20 p-3">
        <div class="min-w-0 flex-1">
          <p v-if="item.title" class="truncate text-sm font-semibold">{{ item.title }}</p>
          <p class="truncate text-xs text-base-content/60">{{ item.url }}</p>
          <p v-if="item.error" class="break-all text-xs text-error">{{ item.error }}</p>
        </div>
        <span class="badge badge-ghost badge-sm shrink-0">{{ statusText(item.status) }}</span>
        <div class="flex shrink-0 flex-wrap justify-end gap-1">
          <button v-if="item.status === 'error' || item.status === 'cancelled'" class="btn btn-xs" type="button" @click="act(retryBatch, item.id)">
            {{ t.retry }}
          </button>
          <button v-if="running && !['done', 'error', 'cancelled'].includes(item.status)" class="btn btn-xs" type="button" @click="act(cancelBatch, item.id)">
            {{ t.cancel }}
          </button>
          <a v-if="item.status === 'done'" class="btn btn-xs btn-outline" :href="asrArchiveUrl(item.file_id)" download>{{ t.downloadArchive }}</a>
          <button class="btn btn-xs btn-ghost" type="button" @click="act(deleteBatch, item.id)">{{ t.remove }}</button>
        </div>
      </li>
    </ul>
  </section>
</template>
