<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useSupabase } from '@/composables/useSupabase'
import type { BotConfig } from '@/types'

const supabase = useSupabase()

const configs = ref<BotConfig[]>([])
const editJson = ref('')
const changeNote = ref('')
const saving = ref(false)
const error = ref('')
const showHistory = ref(false)

async function loadConfigs() {
  const { data } = await supabase
    .from('bot_config')
    .select('*')
    .order('version', { ascending: false })
    .limit(20)
  if (data) {
    configs.value = data as BotConfig[]
    if (data.length > 0) {
      editJson.value = JSON.stringify(data[0].config_json, null, 2)
    }
  }
}

async function saveConfig() {
  saving.value = true
  error.value = ''

  try {
    const parsed = JSON.parse(editJson.value)
    const nextVersion = (configs.value[0]?.version ?? 0) + 1

    const { error: err } = await supabase.from('bot_config').insert({
      version: nextVersion,
      config_json: parsed,
      changed_by: 'frontend',
      change_note: changeNote.value || `v${nextVersion} from dashboard`,
    })

    if (err) throw err

    changeNote.value = ''
    await loadConfigs()
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    saving.value = false
  }
}

function loadVersion(config: BotConfig) {
  editJson.value = JSON.stringify(config.config_json, null, 2)
  showHistory.value = false
}

function formatTime(ts: string) {
  return new Date(ts).toLocaleString('zh-TW')
}

onMounted(loadConfigs)
</script>

<template>
  <div class="p-4 md:p-6 space-y-4 md:space-y-6">
    <div class="flex items-center justify-between">
      <h2 class="text-xl md:text-2xl font-bold">設定</h2>
      <button
        class="md:hidden px-3 py-1.5 bg-(--color-bg-card) border border-(--color-border) rounded-lg text-sm"
        @click="showHistory = !showHistory"
      >
        {{ showHistory ? '編輯器' : '歷史版本' }}
      </button>
    </div>

    <div class="grid grid-cols-1 md:grid-cols-3 gap-4 md:gap-6">
      <!-- JSON Editor -->
      <div class="md:col-span-2 space-y-4" :class="{ 'hidden md:block': showHistory }">
        <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-3 md:p-4">
          <h3 class="text-sm font-semibold text-(--color-text-secondary) uppercase mb-3">設定編輯器</h3>
          <textarea
            v-model="editJson"
            class="w-full h-[50vh] md:h-[500px] bg-(--color-bg-secondary) border border-(--color-border) rounded-lg p-3 text-xs md:text-sm font-mono resize-none focus:outline-none focus:border-(--color-accent)"
            spellcheck="false"
          />
          <div v-if="error" class="text-(--color-danger) text-sm mt-2">{{ error }}</div>
          <div class="flex flex-col sm:flex-row items-stretch sm:items-center gap-3 mt-3">
            <input
              v-model="changeNote"
              placeholder="變更說明（選填）..."
              class="flex-1 bg-(--color-bg-secondary) border border-(--color-border) rounded-lg px-3 py-1.5 text-sm"
            />
            <button
              @click="saveConfig"
              :disabled="saving"
              class="px-4 py-1.5 bg-(--color-accent) text-white rounded-lg text-sm font-medium hover:opacity-90 disabled:opacity-50"
            >
              {{ saving ? '儲存中...' : '儲存新版本' }}
            </button>
          </div>
        </div>
      </div>

      <!-- Version History -->
      <div :class="{ 'hidden md:block': !showHistory }">
        <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-3 md:p-4">
          <h3 class="text-sm font-semibold text-(--color-text-secondary) uppercase mb-3">版本歷史</h3>
          <div class="space-y-2 max-h-[60vh] md:max-h-[580px] overflow-auto">
            <div
              v-for="c in configs" :key="c.id"
              @click="loadVersion(c)"
              class="bg-(--color-bg-secondary) rounded-lg p-3 cursor-pointer hover:border-(--color-accent) border border-transparent transition-colors"
            >
              <div class="flex justify-between items-center">
                <span class="font-mono text-sm font-bold">v{{ c.version }}</span>
                <span class="text-xs text-(--color-text-secondary)">{{ c.changed_by }}</span>
              </div>
              <div class="text-xs text-(--color-text-secondary) mt-1">{{ formatTime(c.created_at) }}</div>
              <div v-if="c.change_note" class="text-xs text-(--color-text-secondary) mt-1">
                {{ c.change_note }}
              </div>
            </div>
            <div v-if="!configs.length" class="text-sm text-(--color-text-secondary)">
              尚無設定版本
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
