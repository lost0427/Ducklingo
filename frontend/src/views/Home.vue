<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ArrowDownTrayIcon, CloudArrowUpIcon, TrashIcon } from '@heroicons/vue/24/outline'
import {
  deleteUploadedFile,
  downloadVideo,
  getUploadConfig,
  listMediaFiles,
  mediaFileUrl,
  uploadVideo,
  type MediaFile,
  type UploadConfig,
  type VideoQuality,
} from '../api'
import { t } from '../prefs'

const url = ref('')
const quality = ref<VideoQuality>('1080')
const downloading = ref(false)
const error = ref('')
const files = ref<MediaFile[]>([])
const previewUrl = ref('')
const selectedFile = ref<File | null>(null)
const uploadConfig = ref<UploadConfig | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const uploading = ref(false)
const uploadProgress = ref(0)
let uploadTask: ReturnType<typeof uploadVideo> | null = null

const qualities: { value: VideoQuality; label: keyof typeof t.value }[] = [
  { value: 'best', label: 'qualityBest' },
  { value: '2160', label: 'quality2160' },
  { value: '1440', label: 'quality1440' },
  { value: '1080', label: 'quality1080' },
  { value: '720', label: 'quality720' },
  { value: '480', label: 'quality480' },
  { value: '360', label: 'quality360' },
]

const currentFile = computed(() => files.value[0] ?? null)
const mediaSrc = computed(() =>
  previewUrl.value || (currentFile.value ? mediaFileUrl(currentFile.value.id) : ''),
)
const displayName = computed(() => selectedFile.value?.name || currentFile.value?.filename || '')
const displaySize = computed(() => selectedFile.value?.size || currentFile.value?.size || 0)
const mediaKind = computed(() => {
  const type = currentFile.value?.contentType ?? ''
  return type.startsWith('audio/') || /\.(wav|mp3|flac|m4a)$/i.test(displayName.value) ? 'audio' : 'video'
})
const uploadHint = computed(() => {
  if (!uploadConfig.value) return t.value.loading
  const formats = uploadConfig.value.extensions.map((extension) => extension.toUpperCase()).join(', ')
  return t.value.fileHint.replace('{formats}', formats).replace('{size}', formatBytes(uploadConfig.value.maxBytes))
})

function formatBytes(bytes: number) {
  if (bytes < 1024) return bytes + ' B'
  const units = ['K', 'M', 'G', 'T']
  let value = bytes
  let unit = -1
  do {
    value /= 1024
    unit += 1
  } while (value >= 1024 && unit < units.length - 1)
  return Math.round(value) + ' ' + units[unit]
}

function messageFrom(errorValue: unknown) {
  const text = errorValue instanceof Error ? errorValue.message : String(errorValue)
  try {
    return (JSON.parse(text) as { detail?: string }).detail || text
  } catch {
    return text
  }
}

async function refreshFiles() {
  try {
    files.value = await listMediaFiles()
  } catch (errorValue) {
    console.error(errorValue)
    if (!previewUrl.value && !files.value.length) error.value = t.value.mediaLoadFailed
  }
}

async function submit() {
  error.value = ''
  downloading.value = true
  try {
    releaseLocalPreview()
    await downloadVideo(url.value.trim(), quality.value)
    selectedFile.value = null
    if (fileInput.value) fileInput.value.value = ''
    await refreshFiles()
  } catch (errorValue) {
    error.value = messageFrom(errorValue)
  } finally {
    downloading.value = false
  }
}

function setLocalPreview(file: File) {
  releaseLocalPreview()
  previewUrl.value = URL.createObjectURL(file)
}

function releaseLocalPreview() {
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
  previewUrl.value = ''
}

async function removeMedia() {
  uploadTask?.abort()
  uploadTask = null
  uploading.value = false
  uploadProgress.value = 0
  selectedFile.value = null
  releaseLocalPreview()
  const target = currentFile.value
  if (target) {
    try {
      await deleteUploadedFile(target.id)
    } catch (errorValue) {
      error.value = messageFrom(errorValue)
    }
    await refreshFiles()
  }
  if (fileInput.value) fileInput.value.value = ''
}

function downloadCurrent() {
  if (!mediaSrc.value) return
  const link = document.createElement('a')
  link.href = mediaSrc.value
  link.download = displayName.value
  link.click()
  link.remove()
}

async function uploadSelectedFile(file: File) {
  uploading.value = true
  uploadProgress.value = 0
  const task = uploadVideo(file, (value) => (uploadProgress.value = value))
  uploadTask = task
  try {
    await task.promise
    if (selectedFile.value === file) {
      await refreshFiles()
      selectedFile.value = null
      releaseLocalPreview()
    }
  } catch (errorValue) {
    if (selectedFile.value === file && uploading.value) error.value = messageFrom(errorValue)
  } finally {
    if (uploadTask === task) uploadTask = null
    if (selectedFile.value === file) uploading.value = false
  }
}

function chooseFile(chosen: FileList | File[] | undefined) {
  const list = chosen ? Array.from(chosen) : []
  if (!list.length) return
  if (list.length > 1) {
    selectedFile.value = null
    error.value = t.value.onlyOneFile
    return
  }
  if (!uploadConfig.value) {
    error.value = t.value.loading
    return
  }
  const file = list[0]
  const extension = file.name.toLowerCase().split('.').pop() || ''
  if (!uploadConfig.value.extensions.includes(extension)) {
    selectedFile.value = null
    error.value = t.value.invalidFileFormat
    return
  }
  if (file.size > uploadConfig.value.maxBytes) {
    selectedFile.value = null
    error.value = t.value.fileTooLarge.replace('{size}', formatBytes(uploadConfig.value.maxBytes))
    return
  }
  error.value = ''
  uploadTask?.abort()
  setLocalPreview(file)
  selectedFile.value = file
  void uploadSelectedFile(file)
}

function onFileChange(event: Event) {
  chooseFile((event.target as HTMLInputElement).files)
}

function onDrop(event: DragEvent) {
  chooseFile(event.dataTransfer?.files)
}

onMounted(() => {
  getUploadConfig()
    .then((config) => (uploadConfig.value = config))
    .catch(() => (error.value = t.value.uploadConfigFailed))
  void refreshFiles()
})
onUnmounted(() => {
  uploadTask?.abort()
  releaseLocalPreview()
})
</script>

<template>
  <section class="mx-auto max-w-2xl py-4 sm:py-8">
    <h1 class="mb-4 text-3xl font-bold tracking-tight sm:text-4xl"><span class="text-base-content/80">a.</span> {{ t.downloadOrUpload }}</h1>
    <form class="space-y-4 rounded-box border border-base-content/30 p-3 sm:p-4" @submit.prevent="submit">
      <div v-if="!mediaSrc" class="grid gap-4 sm:grid-cols-[minmax(0,1fr)_9rem]">
        <div class="space-y-2">
          <label for="video-url" class="label p-0 text-sm font-semibold">{{ t.videoUrl }}</label>
          <input
            id="video-url"
            v-model="url"
            class="input w-full bg-base-200"
            type="url"
            inputmode="url"
            autocomplete="url"
            required
          />
        </div>
        <div class="space-y-2">
          <label for="video-quality" class="label p-0 text-sm font-semibold">{{ t.quality }}</label>
          <select id="video-quality" v-model="quality" class="select w-full bg-base-200" :aria-label="t.quality">
            <option v-for="option in qualities" :key="option.value" :value="option.value">
              {{ t[option.label] }}
            </option>
          </select>
        </div>
      </div>

      <button
        v-if="!mediaSrc"
        class="btn btn-outline w-full border-base-content/80"
        type="submit"
        :disabled="downloading"
        :aria-busy="downloading"
      >
        <span v-if="downloading" class="loading loading-spinner loading-sm" aria-hidden="true"></span>
        <ArrowDownTrayIcon v-else class="size-5" aria-hidden="true" />
        {{ downloading ? t.downloading : t.download }}
      </button>

      <div v-if="mediaSrc" class="card bg-base-100 shadow-sm">
        <div class="card-body gap-3 p-4">
          <div class="flex items-center justify-between gap-3 text-sm">
            <span class="truncate font-semibold">{{ displayName }}</span>
            <span v-if="uploading" class="badge badge-primary badge-sm shrink-0">{{ t.uploading }} {{ uploadProgress }}%</span>
            <span v-else class="badge badge-ghost badge-sm shrink-0">{{ formatBytes(displaySize) }}</span>
          </div>
          <div class="relative">
            <video
              v-if="mediaKind === 'video'"
              class="aspect-video w-full rounded-box bg-neutral object-contain"
              controls
              preload="metadata"
              :src="mediaSrc"
              :aria-label="displayName"
            ></video>
            <audio v-else class="w-full" controls :src="mediaSrc" :aria-label="displayName"></audio>
            <div
              v-if="uploading"
              role="progressbar"
              class="absolute inset-x-0 bottom-0 h-1"
              :aria-label="t.uploading"
              :aria-valuenow="uploadProgress"
              aria-valuemin="0"
              aria-valuemax="100"
            >
              <div
                class="h-full bg-primary transition-[width] duration-300 ease-out"
                :style="{ width: uploadProgress + '%' }"
              ></div>
            </div>
          </div>
          <div class="card-actions">
            <button class="btn btn-outline flex-1 border-base-content/80" type="button" @click="removeMedia">
              <TrashIcon class="size-5" aria-hidden="true" />
              {{ t.deleteVideo }}
            </button>
            <button class="btn btn-outline flex-1 border-base-content/80" type="button" @click="downloadCurrent">
              <ArrowDownTrayIcon class="size-5" aria-hidden="true" />
              {{ t.download }}
            </button>
          </div>
        </div>
      </div>

      <div v-if="!mediaSrc" class="space-y-2">
        <p class="text-sm font-semibold">{{ t.orUpload }}</p>
        <div
          class="flex min-h-16 items-center gap-3 rounded-field bg-base-200 p-3"
          @dragover.prevent
          @drop.prevent="onDrop"
        >
          <CloudArrowUpIcon class="size-9 shrink-0 text-base-content/60" aria-hidden="true" />
          <div class="min-w-0 flex-1">
            <p class="truncate text-sm font-semibold">{{ selectedFile?.name || t.dropFile }}</p>
            <p class="truncate text-xs text-base-content/60">
              {{ selectedFile ? formatBytes(displaySize) : uploadHint }}
            </p>
          </div>
          <label for="video-file" class="btn btn-sm shrink-0">{{ t.browseFiles }}</label>
          <input ref="fileInput" id="video-file" class="hidden" type="file" :accept="uploadConfig?.accept" @change="onFileChange" />
        </div>
      </div>

      <div v-if="error" role="alert" class="alert alert-error">
        <span>{{ error }}</span>
      </div>
    </form>
  </section>
</template>
