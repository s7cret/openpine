import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as api from '@/api/client'

export const usePineFilesStore = defineStore('pineFiles', () => {
  const items = ref<any[]>([])
  const currentContent = ref('')
  const loading = ref(false)
  const compiling = ref<Set<string>>(new Set())

  async function fetchAll() {
    loading.value = true
    try {
      const { data } = await api.getPineFiles()
      items.value = data ?? []
    } catch (e) { console.error(e) }
    loading.value = false
  }

  async function fetchContent(id: string) {
    try {
      const { data } = await api.getPineFile(id)
      currentContent.value = data?.source_text ?? data?.content ?? JSON.stringify(data, null, 2)
    } catch (e) {
      currentContent.value = 'Error loading content'
    }
  }

  async function create(name: string, sourceText: string): Promise<{ sourceId?: string; error?: string }> {
    try {
      const { data } = await api.createPineFile({ name, source_text: sourceText, source_type: 'unknown' })
      const sourceId = data?.id
      await fetchAll()

      // Auto-compile after creation
      if (sourceId) {
        compiling.value = new Set([...compiling.value, sourceId])
        try {
          await api.compilePineFile(sourceId)
          compiling.value = new Set([...compiling.value].filter(id => id !== sourceId))
          await fetchAll() // Refresh to get artifact info
        } catch (compileErr: any) {
          compiling.value = new Set([...compiling.value].filter(id => id !== sourceId))
          const msg = compileErr?.response?.data?.detail ?? compileErr?.message ?? 'Compile failed'
          return { sourceId, error: `Source created but compile failed: ${msg}` }
        }
      }
      return { sourceId }
    } catch (e: any) {
      const msg = e?.response?.data?.detail ?? e?.message ?? 'Unknown error'
      console.error('Create pine file failed:', e)
      return { error: `Failed to create: ${msg}` }
    }
  }

  async function remove(id: string) {
    try {
      const preview = await api.previewDeletePineFile(id).then((r) => r.data).catch(() => null)
      if (preview) {
        const resources = Object.entries(preview.resources ?? {})
          .filter(([, value]) => Number(value) > 0)
          .map(([key, value]) => `${key}: ${value}`)
          .join('\n')
        const ok = confirm(`Delete Pine file "${preview.name ?? id}"?\n\nWill delete:\n${resources || 'source row only'}\n\nStrategies deleted: 0\nMarket bars deleted: 0`)
        if (!ok) return
      } else if (!confirm(`Delete Pine file ${id}? Strategies and market bars will not be deleted.`)) {
        return
      }
      await api.deletePineFile(id)
      items.value = items.value.filter((f: any) => (f.id ?? f.source_id) !== id)
    } catch (e) { console.error(e) }
  }

  return { items, currentContent, loading, compiling, fetchAll, fetchContent, create, remove }
})
