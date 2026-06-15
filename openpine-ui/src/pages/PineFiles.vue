<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { usePineFilesStore } from '@/stores/pineFiles'
import { readPineFile, normalizePineFileName } from '@/lib/pineFileUpload'

const { t } = useI18n()
const store = usePineFilesStore()
const showAdd = ref(false)
const newName = ref('')
const newContent = ref('')
const expandedId = ref<string | null>(null)
const copied = ref(false)
const createStatus = ref('')
const createLoading = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)
const uploadStatus = ref('')
const uploadError = ref(false)

onMounted(() => store.fetchAll())

/**
 * Wire the <input type="file"> picker to the same fields the manual
 * textarea uses.  Reading the file client-side avoids a multi-megabyte
 * round-trip just to set `newContent` — the user sees the parsed
 * contents in the textarea, can tweak if they want, then clicks Save.
 */
async function onPineFileSelected(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  uploadStatus.value = ''
  uploadError.value = false
  if (!file) return
  try {
    const text = await readPineFile(file)
    newContent.value = text
    // If the user hasn't typed a name yet, default to the file's
    // basename.  normalizePineFileName trims dir prefix + ensures .pine.
    if (!newName.value.trim()) {
      newName.value = normalizePineFileName(file.name)
    }
    uploadStatus.value = t('pineFiles.uploaded', {
      name: file.name,
      bytes: file.size,
    })
    uploadError.value = false
  } catch (e: any) {
    uploadStatus.value = e?.message ?? String(e)
    uploadError.value = true
  } finally {
    // Reset the input so picking the same file twice still triggers change.
    if (fileInput.value) fileInput.value.value = ''
  }
}

// Filter state
const filterName = ref('')
const filteredFiles = computed(() => {
  if (!filterName.value) return store.items
  const q = filterName.value.toLowerCase()
  return store.items.filter((f: any) => (f.name ?? '').toLowerCase().includes(q))
})

function getId(file: any) {
  return file.id ?? file.source_id ?? ''
}

function rawFileName(file: any) {
  return String(file.name ?? '').trim()
}

function fileLeafName(file: any) {
  const raw = rawFileName(file)
  if (!raw) return '—'
  const parts = raw.replace(/\\/g, '/').split('/').filter(Boolean)
  return parts[parts.length - 1] ?? raw
}

function fileStem(file: any) {
  return fileLeafName(file).replace(/\.[^.]+$/, '') || fileLeafName(file)
}

function fileExt(file: any) {
  const match = fileLeafName(file).match(/(\.[^.]+)$/)
  return match?.[1]?.toLowerCase() ?? ''
}

function fileParentPath(file: any) {
  const raw = rawFileName(file).replace(/\\/g, '/')
  const leaf = fileLeafName(file)
  if (!raw || raw === leaf) return ''
  return raw.slice(0, Math.max(0, raw.length - leaf.length)).replace(/\/$/, '')
}

async function toggleExpand(file: any) {
  const id = getId(file)
  if (expandedId.value === id) {
    expandedId.value = null
    return
  }
  expandedId.value = id
  await store.fetchContent(id)
}

async function addFile() {
  if (!newName.value.trim()) return
  createLoading.value = true
  createStatus.value = t('pineFiles.createAndCompile')
  const result = await store.create(newName.value.trim(), newContent.value)
  createLoading.value = false
  if (result.error) {
    createStatus.value = t('pineFiles.createFailedPrefix', { error: result.error })
    return
  }
  if (result.compileQueued) {
    const op = result.operationId ? t('pineFiles.compileOpSuffix', { id: result.operationId.slice(0, 12) }) : ''
    createStatus.value = t('pineFiles.createdQueued', { op })
    newName.value = ''
    newContent.value = ''
    setTimeout(() => { showAdd.value = false; createStatus.value = '' }, 2500)
    return
  }
  createStatus.value = t('pineFiles.createdAndCompiled')
  newName.value = ''
  newContent.value = ''
  setTimeout(() => { showAdd.value = false; createStatus.value = '' }, 1500)
}

async function copyContent() {
  try {
    await navigator.clipboard.writeText(store.currentContent)
    copied.value = true
    setTimeout(() => copied.value = false, 2000)
  } catch (e) {
    // fallback
    const ta = document.createElement('textarea')
    ta.value = store.currentContent
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
    copied.value = true
    setTimeout(() => copied.value = false, 2000)
  }
}
</script>

<template>
  <div class="space-y-4">
    <!-- Header -->
    <div class="flex items-center justify-between gap-3">
      <h1 class="min-w-0 truncate text-lg font-semibold text-gray-200">{{ t('pineFiles.title') }}</h1>
      <button
        @click="showAdd = !showAdd"
        class="shrink-0 px-3 py-1.5 bg-accent hover:bg-accent-dark text-white text-sm rounded-lg transition-colors"
      >
        {{ t('pineFiles.addFile') }}
      </button>
    </div>

    <!-- Add Form -->
    <transition name="fade">
      <div v-if="showAdd" class="bg-dark-800 rounded-xl border border-dark-500 p-4 space-y-3">
        <input
          v-model="newName"
          :placeholder="t('pineFiles.newFileName')"
          class="w-full bg-dark-700 border border-dark-500 rounded-lg px-3 py-2 text-sm text-gray-200 placeholder-gray-500 focus:outline-none focus:border-accent"
        />
        <div class="flex flex-wrap items-center gap-2">
          <input
            ref="fileInput"
            type="file"
            accept=".pine,text/plain"
            data-testid="pine-file-input"
            class="block w-full max-w-xs text-xs text-gray-300 file:mr-3 file:px-3 file:py-1.5 file:rounded-lg file:border-0 file:bg-accent file:text-white file:text-sm file:cursor-pointer hover:file:bg-accent-dark"
            @change="onPineFileSelected"
          />
          <span
            v-if="uploadStatus"
            class="text-xs"
            :class="uploadError ? 'text-danger' : 'text-success'"
            data-testid="pine-file-status"
          >{{ uploadStatus }}</span>
        </div>
        <textarea
          v-model="newContent"
          :placeholder="t('pineFiles.newFileContent')"
          rows="8"
          class="w-full bg-dark-700 border border-dark-500 rounded-lg px-3 py-2 text-sm text-gray-200 font-mono placeholder-gray-500 focus:outline-none focus:border-accent resize-y"
        />
        <div class="flex gap-2 justify-end items-center">
          <span v-if="createStatus" class="text-xs" :class="createStatus.startsWith('❌') ? 'text-danger' : 'text-success'">{{ createStatus }}</span>
          <button @click="showAdd = false; createStatus = ''" class="px-3 py-1.5 text-sm text-gray-400 hover:text-gray-200">{{ t('common.cancel') }}</button>
          <button @click="addFile" :disabled="createLoading" class="px-4 py-1.5 bg-accent hover:bg-accent-dark text-white text-sm rounded-lg disabled:opacity-50">
            {{ createLoading ? t('pineFiles.compiling') : t('common.save') }}
          </button>
        </div>
      </div>
    </transition>

    <!-- Filter Bar -->
    <div class="bg-dark-800 rounded-xl border border-dark-500 p-3 flex flex-wrap gap-2 items-center">
      <input
        v-model="filterName"
        :placeholder="t('pineFiles.searchPlaceholder')"
        class="w-full bg-dark-700 border border-dark-500 rounded-lg px-3 py-1.5 text-sm text-gray-200 placeholder-gray-500 focus:outline-none focus:border-accent sm:w-64"
      />
      <span v-if="filteredFiles.length !== store.items.length" class="text-xs text-gray-500 ml-auto">
        {{ filteredFiles.length }} / {{ store.items.length }}
      </span>
    </div>

    <!-- Table -->
    <div class="max-w-full overflow-hidden rounded-xl border border-dark-500 bg-dark-800">
      <table class="w-full table-fixed text-sm">
        <thead>
          <tr class="text-xs text-gray-500 uppercase tracking-wider border-b border-dark-600">
            <th class="px-3 py-2.5 text-left sm:px-4">{{ t('pineFiles.thName') }}</th>
            <th class="hidden px-4 py-2.5 text-left sm:table-cell sm:w-24">{{ t('pineFiles.thType') }}</th>
            <th class="hidden px-4 py-2.5 text-left sm:table-cell sm:w-20">{{ t('pineFiles.thVersion') }}</th>
            <th class="hidden px-4 py-2.5 text-left md:table-cell md:w-28">{{ t('pineFiles.thCreated') }}</th>
            <th class="w-9 px-2 py-2.5 sm:w-10 sm:px-4"></th>
            <th class="w-9 px-2 py-2.5 sm:w-10"></th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="filteredFiles.length === 0">
            <td colspan="6" class="px-4 py-8 text-center text-gray-500">
              {{ store.loading ? t('common.loading') : (store.items.length === 0 ? t('pineFiles.noFiles') : t('pineFiles.noMatchSearch')) }}
            </td>
          </tr>
          <template v-for="file in filteredFiles" :key="getId(file)">
            <tr
              class="border-b border-dark-600/50 hover:bg-dark-700/50 cursor-pointer transition-colors"
              @click="toggleExpand(file)"
            >
              <td class="min-w-0 px-3 py-2.5 font-medium text-gray-200 sm:px-4">
                <div class="min-w-0">
                  <div class="flex min-w-0 items-center gap-2">
                    <span class="min-w-0 truncate leading-snug" :title="rawFileName(file)">{{ fileStem(file) }}</span>
                    <span v-if="fileExt(file)" class="shrink-0 rounded border border-accent/30 bg-accent/10 px-1.5 py-0.5 font-mono text-[10px] text-accent-light">
                      {{ fileExt(file) }}
                    </span>
                    <span v-if="store.compiling.has(getId(file))" class="inline-flex shrink-0 items-center gap-1 text-xs text-accent">
                      <svg class="animate-spin h-3 w-3" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" fill="none"/><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg>
                      {{ t('pineFiles.compiling') }}
                    </span>
                  </div>
                  <div v-if="fileParentPath(file)" class="mt-0.5 truncate font-mono text-[10px] font-normal text-gray-500" :title="rawFileName(file)">
                    {{ fileParentPath(file) }}
                  </div>
                </div>
              </td>
              <td class="hidden px-4 py-2.5 text-gray-400 text-xs sm:table-cell">{{ file.source_type ?? '—' }}</td>
              <td class="hidden px-4 py-2.5 text-gray-400 text-xs sm:table-cell">{{ t('pineFiles.versionPrefix') }}{{ file.version ?? '—' }}</td>
              <td class="hidden px-4 py-2.5 text-gray-400 text-xs md:table-cell">{{ file.created_at ? new Date(file.created_at).toLocaleDateString() : '—' }}</td>
              <td class="px-2 py-2.5 text-center sm:px-4">
                <span class="text-gray-500 transition-transform inline-block" :class="expandedId === getId(file) ? 'rotate-90' : ''">▶</span>
              </td>
              <td class="px-2 py-2.5 text-center">
                <button
                  @click.stop="store.remove(getId(file))"
                  class="p-1 rounded hover:bg-danger/20 text-gray-500 hover:text-danger transition-colors"
                  :title="t('pineFiles.deleteFile')"
                >
                  🗑
                </button>
              </td>
            </tr>
            <!-- Expanded content -->
            <tr v-if="expandedId === getId(file)">
              <td colspan="6" class="min-w-0 px-3 py-3 bg-dark-900/50 sm:px-4">
                <div class="mb-2 flex min-w-0 items-center justify-between gap-2">
                  <div class="min-w-0">
                    <span class="text-xs text-gray-500">{{ t('pineFiles.sourceLabel') }}</span>
                    <div class="mt-1 flex min-w-0 items-center gap-2 text-sm font-medium text-gray-200">
                      <span class="min-w-0 truncate" :title="rawFileName(file)">{{ fileStem(file) }}</span>
                      <span v-if="fileExt(file)" class="shrink-0 rounded border border-accent/30 bg-accent/10 px-1.5 py-0.5 font-mono text-[10px] text-accent-light">
                        {{ fileExt(file) }}
                      </span>
                    </div>
                  </div>
                  <button
                    @click.stop="copyContent()"
                    class="shrink-0 px-2 py-1 text-xs rounded bg-dark-600 hover:bg-dark-500 text-gray-300 transition-colors"
                  >
                    {{ copied ? t('pineFiles.copied') : t('pineFiles.copy') }}
                  </button>
                </div>
                <div class="max-w-full min-w-0 overflow-hidden">
                  <pre class="block max-h-64 max-w-full overflow-auto rounded-lg bg-dark-900 p-3 text-xs text-gray-300 font-mono whitespace-pre">{{ store.currentContent || t('common.loading') }}</pre>
                </div>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
  </div>
</template>
