<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ArrowDownTrayIcon, CloudArrowUpIcon, TrashIcon } from '@heroicons/vue/24/outline'
import { downloadVideo, getUploadConfig, type UploadConfig, type VideoQuality } from '../api'
import { t } from '../prefs'

const url = ref('')
const quality = ref<VideoQuality>('1080')
const downloading = ref(false)
const error = ref('')
const downloadedUrl = ref('')
const downloadedFilename = ref('ducklingo-video.mp4')
const selectedFile = ref<File | null>(null)
const uploadConfig = ref<UploadConfig | null>(null)

const qualities: { value: VideoQuality; label: keyof typeof t.value }[] = [
  { value: 'best', label: 'qualityBest' },
  { value: '2160', label: 'quality2160' },
  { value: '1440', label: 'quality1440' },
  { value: '1080', label: 'quality1080' },
  { value: '720', label: 'quality720' },
  { value: '480', label: 'quality480' },
  { value: '360', label: 'quality360' },
]

const selectedFileSize = computed(() => (selectedFile.value ? formatBytes(selectedFile.value.size) : ''))
const uploadHint = computed(() => {
  if (!uploadConfig.value) return t.value.loading
  const formats = uploadConfig.value.extensions.map((extension) => extension.toUpperCase()).join(', ')
  return t.value.fileHint.replace('{formats}', formats).replace('{size}', formatBytes(uploadConfig.value.maxBytes))
})

function formatBytes(bytes: number) {
  if (bytes < 1024) return bytes + ' B'
  const units = ['KB', 'MB', 'GB', 'TB']
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

async function submit() {
  error.value = ''
  downloading.value = true
  try {
    const result = await downloadVideo(url.value.trim(), quality.value)
    const objectUrl = URL.createObjectURL(result.blob)
    downloadedUrl.value = objectUrl
    downloadedFilename.value = result.filename
    const link = document.createElement('a')
    link.href = objectUrl
    link.download = result.filename
    link.click()
    link.remove()
  } catch (errorValue) {
    error.value = messageFrom(errorValue)
  } finally {
    downloading.value = false
  }
}

function removeVideo() {
  if (downloadedUrl.value) URL.revokeObjectURL(downloadedUrl.value)
  downloadedUrl.value = ''
  downloadedFilename.value = 'ducklingo-video.mp4'
}

function chooseFile(files: FileList | File[] | undefined) {
  const list = files ? Array.from(files) : []
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
  selectedFile.value = file
}

function onFileChange(event: Event) {
  chooseFile((event.target as HTMLInputElement).files)
}

function onDrop(event: DragEvent) {
  chooseFile(event.dataTransfer?.files)
}

onMounted(async () => {
  try {
    uploadConfig.value = await getUploadConfig()
  } catch {
    error.value = t.value.uploadConfigFailed
  }
})
onUnmounted(removeVideo)
</script>

<template>
  <section class="mx-auto max-w-2xl py-4 sm:py-8">
    <h1 class="mb-4 text-3xl font-bold tracking-tight sm:text-4xl"><span class="text-base-content/80">a.</span> {{ t.downloadOrUpload }}</h1>
    <form class="space-y-4 rounded-box border border-base-content/30 p-3 sm:p-4" @submit.prevent="submit">
      <div class="grid gap-4 sm:grid-cols-[minmax(0,1fr)_9rem]">
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
        v-if="!downloadedUrl"
        class="btn btn-outline w-full border-base-content/80"
        type="submit"
        :disabled="downloading"
        :aria-busy="downloading"
      >
        <span v-if="downloading" class="loading loading-spinner loading-sm" aria-hidden="true"></span>
        <ArrowDownTrayIcon v-else class="size-5" aria-hidden="true" />
        {{ downloading ? t.downloading : t.download }}
      </button>

      <div v-if="downloadedUrl" class="space-y-3">
        <video
          class="aspect-video w-full rounded-box border border-base-300 bg-neutral object-contain"
          controls
          preload="metadata"
          :src="downloadedUrl"
          :aria-label="downloadedFilename"
        ></video>
        <button class="btn btn-outline btn-error" type="button" @click="removeVideo">
          <TrashIcon class="size-5" aria-hidden="true" />
          {{ t.deleteVideo }}
        </button>
      </div>

      <div class="space-y-2">
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
              {{ selectedFile ? selectedFileSize : uploadHint }}
            </p>
          </div>
          <label for="video-file" class="btn btn-sm shrink-0">{{ t.browseFiles }}</label>
          <input id="video-file" class="hidden" type="file" :accept="uploadConfig?.accept" @change="onFileChange" />
        </div>
      </div>

      <div v-if="error" role="alert" class="alert alert-error">
        <span>{{ error }}</span>
      </div>
    </form>
  </section>
</template>
