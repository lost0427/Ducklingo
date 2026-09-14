<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
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

const urls = ref('')
const items = ref<BatchItem[]>([])
const error = ref('')
const options = reactive<BatchOptions>({
  quality: '1080',
  language: '',
  target_language: '简体中文',
  mode: 'standard',
  reasoning_effort: 'off',
  context_window_tokens: '131072',
  max_context_ratio: '0.8',
})
const qualities = [
  { value: 'best', label: t.value.qualityBest },
  { value: '1080', label: t.value.quality1080 },
  { value: '720', label: t.value.quality720 },
  { value: '480', label: t.value.quality480 },
]
const running = computed(() => items.value.some((item) => !['done', 'error', 'cancelled'].includes(item.status)))
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

let timer: number | undefined
let optionsLoaded = false

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

async function saveOptions() {
  error.value = ''
  try {
    await putBatchOptions(options)
  } catch (err) {
    error.value = errorMessage(err)
  }
}

async function add() {
  const list = urls.value.split('\n').map((line) => line.trim()).filter(Boolean)
  if (!list.length) {
    error.value = t.value.noValidUrls
    return
  }
  error.value = ''
  try {
    await putBatchOptions(options)
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

function setMode(event: Event) {
  options.mode = (event.target as HTMLInputElement).checked ? 'reflect' : 'standard'
}

onMounted(() => {
  void load()
  timer = window.setInterval(() => {
    if (running.value) void load()
  }, 2000)
})
onUnmounted(() => window.clearInterval(timer))
</script>

<template>
  <section class="mx-auto max-w-3xl py-4 sm:py-8">
    <h1 class="mb-4 text-3xl font-bold tracking-tight sm:text-4xl">{{ t.tasksTitle }}</h1>
    <div class="space-y-4 rounded-box border border-base-content/30 p-3 sm:p-4">
      <fieldset class="fieldset">
        <label class="label p-0 text-sm font-semibold" for="batch-urls">{{ t.urlsLabel }}</label>
        <textarea id="batch-urls" v-model="urls" rows="5" class="textarea w-full bg-base-200 font-mono text-sm"></textarea>
      </fieldset>
      <div class="grid gap-3 sm:grid-cols-2">
        <fieldset class="fieldset">
          <label class="label p-0 text-sm font-semibold" for="batch-quality">{{ t.quality }}</label>
          <select id="batch-quality" v-model="options.quality" class="select w-full bg-base-200">
            <option v-for="option in qualities" :key="option.value" :value="option.value">{{ option.label }}</option>
          </select>
        </fieldset>
        <fieldset class="fieldset">
          <label class="label p-0 text-sm font-semibold" for="batch-language">{{ t.asrLanguage }}</label>
          <input id="batch-language" v-model="options.language" class="input w-full bg-base-200" />
        </fieldset>
        <fieldset class="fieldset">
          <label class="label p-0 text-sm font-semibold" for="batch-target">{{ t.targetLanguage }}</label>
          <input id="batch-target" v-model="options.target_language" class="input w-full bg-base-200" />
        </fieldset>
        <fieldset class="fieldset">
          <label class="label p-0 text-sm font-semibold" for="batch-reasoning">{{ t.reasoningEffort }}</label>
          <select id="batch-reasoning" v-model="options.reasoning_effort" class="select w-full bg-base-200">
            <option value="off">{{ t.reasoningOff }}</option>
            <option value="low">{{ t.reasoningLow }}</option>
            <option value="medium">{{ t.reasoningMedium }}</option>
            <option value="high">{{ t.reasoningHigh }}</option>
          </select>
        </fieldset>
      </div>
      <label class="label cursor-pointer justify-start gap-3 p-0">
        <input class="toggle toggle-primary" type="checkbox" :checked="options.mode === 'reflect'" @change="setMode" />
        <span>{{ options.mode === 'reflect' ? t.reflectMode : t.standardMode }}</span>
      </label>
      <div class="flex flex-wrap items-center gap-2">
        <button class="btn btn-outline" type="button" @click="saveOptions">{{ t.save }}</button>
        <button class="btn btn-primary flex-1" type="button" @click="add">{{ t.addToQueue }}</button>
      </div>
      <p v-if="error" role="alert" class="alert alert-error"><span class="break-all">{{ error }}</span></p>
    </div>

    <ul class="mt-4 space-y-2">
      <li v-if="!items.length" class="text-sm text-base-content/60">{{ t.queueEmpty }}</li>
      <li v-for="item in items" :key="item.id" class="flex items-start gap-3 rounded-box border border-base-content/20 p-3">
        <div class="min-w-0 flex-1">
          <p class="truncate text-sm">{{ item.url }}</p>
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
          <a v-if="item.file_id" class="btn btn-xs btn-outline" :href="asrArchiveUrl(item.file_id)" download>{{ t.downloadArchive }}</a>
          <button class="btn btn-xs btn-ghost" type="button" @click="act(deleteBatch, item.id)">{{ t.remove }}</button>
        </div>
      </li>
    </ul>
  </section>
</template>
