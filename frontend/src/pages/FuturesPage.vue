<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useBotStore } from '@/stores/bot'
import { useSupabase } from '@/composables/useSupabase'
import type { FuturesMargin } from '@/types'

const bot = useBotStore()
const supabase = useSupabase()

const filteredFuturesPositions = computed(() =>
  bot.futuresPositions.filter(p => (p.mode ?? 'live') === bot.globalMode),
)

const marginHistory = ref<FuturesMargin[]>([])

onMounted(async () => {
  bot.fetchFuturesMargin()
  bot.fetchFuturesFunding()

  // Load margin history for chart
  const { data } = await supabase
    .from('futures_margin')
    .select('*')
    .order('created_at', { ascending: false })
    .limit(100)
  if (data) marginHistory.value = (data as FuturesMargin[]).reverse()
})

const marginRatioPct = computed(() => {
  if (!bot.futuresMargin) return 0
  return bot.futuresMargin.margin_ratio * 100
})

const marginRatioColor = computed(() => {
  const r = marginRatioPct.value
  if (r >= 80) return 'text-(--color-danger)'
  if (r >= 50) return 'text-amber-500'
  return 'text-(--color-success)'
})

const marginRatioBg = computed(() => {
  const r = marginRatioPct.value
  if (r >= 80) return 'bg-(--color-danger)'
  if (r >= 50) return 'bg-amber-500'
  return 'bg-(--color-success)'
})

function formatTime(iso: string): string {
  return new Date(iso).toLocaleString('zh-TW', {
    month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
    hour12: false,
  })
}

function livePrice(symbol: string, fallback: number): number {
  return bot.latestPrices[symbol] ?? fallback
}

function calcPnl(pos: { symbol: string; entry_price: number; quantity: number; current_price: number; side?: string; leverage?: number }) {
  const price = livePrice(pos.symbol, pos.current_price)
  const direction = pos.side === 'short' ? -1 : 1
  const pnl = (price - pos.entry_price) * pos.quantity * direction
  const pnlPct = pos.entry_price > 0 ? ((price - pos.entry_price) / pos.entry_price) * 100 * direction * (pos.leverage ?? 1) : 0
  return { pnl, pnlPct }
}
</script>

<template>
  <div class="p-4 md:p-6 space-y-4 md:space-y-6">
    <h2 class="text-2xl md:text-3xl font-bold">合約</h2>

    <!-- Margin Account Summary -->
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
      <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-3 md:p-4 shadow-sm dark:shadow-none">
        <div class="text-sm text-(--color-text-secondary)">錢包餘額</div>
        <div class="text-xl md:text-2xl font-bold font-mono mt-1">
          {{ bot.futuresMargin ? bot.futuresMargin.total_wallet_balance.toLocaleString('en', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '--' }}
        </div>
        <div class="text-xs text-(--color-text-secondary) mt-0.5">USDT</div>
      </div>

      <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-3 md:p-4 shadow-sm dark:shadow-none">
        <div class="text-sm text-(--color-text-secondary)">可用餘額</div>
        <div class="text-xl md:text-2xl font-bold font-mono mt-1">
          {{ bot.futuresMargin ? bot.futuresMargin.available_balance.toLocaleString('en', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '--' }}
        </div>
        <div class="text-xs text-(--color-text-secondary) mt-0.5">USDT</div>
      </div>

      <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-3 md:p-4 shadow-sm dark:shadow-none">
        <div class="text-sm text-(--color-text-secondary)">未實現損益</div>
        <div class="text-xl md:text-2xl font-bold font-mono mt-1"
             :class="bot.futuresMargin && bot.futuresMargin.total_unrealized_pnl >= 0 ? 'text-(--color-success)' : 'text-(--color-danger)'">
          {{ bot.futuresMargin ? (bot.futuresMargin.total_unrealized_pnl >= 0 ? '+' : '') + bot.futuresMargin.total_unrealized_pnl.toLocaleString('en', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '--' }}
        </div>
        <div class="text-xs text-(--color-text-secondary) mt-0.5">USDT</div>
      </div>

      <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-3 md:p-4 shadow-sm dark:shadow-none">
        <div class="text-sm text-(--color-text-secondary)">保證金比率</div>
        <div class="text-xl md:text-2xl font-bold font-mono mt-1" :class="marginRatioColor">
          {{ bot.futuresMargin ? marginRatioPct.toFixed(1) + '%' : '--' }}
        </div>
        <div class="w-full bg-(--color-bg-secondary) rounded-full h-1.5 mt-2">
          <div class="h-1.5 rounded-full transition-all duration-500" :class="marginRatioBg"
               :style="{ width: Math.min(marginRatioPct, 100) + '%' }" />
        </div>
      </div>
    </div>

    <!-- Positions -->
    <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl shadow-sm dark:shadow-none">
      <div class="p-4 border-b border-(--color-border)">
        <h3 class="font-semibold text-lg">持倉</h3>
      </div>

      <div v-if="filteredFuturesPositions.length === 0" class="p-8 text-center text-(--color-text-secondary)">
        目前無合約持倉
      </div>

      <div v-else class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead>
            <tr class="text-(--color-text-secondary) border-b border-(--color-border)">
              <th class="text-left p-3">幣對</th>
              <th class="text-left p-3">方向</th>
              <th class="text-right p-3">槓桿</th>
              <th class="text-right p-3">數量</th>
              <th class="text-right p-3">入場價</th>
              <th class="text-right p-3">現價</th>
              <th class="text-right p-3">未實現損益</th>
              <th class="text-right p-3">清算價</th>
              <th class="text-right p-3">SL / TP</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="pos in filteredFuturesPositions" :key="pos.id"
                class="border-b border-(--color-border) last:border-0 hover:bg-(--color-bg-secondary)/50 transition-colors">
              <td class="p-3 font-medium">{{ pos.symbol }}</td>
              <td class="p-3">
                <span class="px-2 py-0.5 rounded text-xs font-bold"
                      :class="pos.side === 'long'
                        ? 'bg-(--color-success)/15 text-(--color-success)'
                        : 'bg-(--color-danger)/15 text-(--color-danger)'">
                  {{ pos.side === 'long' ? 'LONG' : 'SHORT' }}
                </span>
              </td>
              <td class="p-3 text-right font-mono">{{ pos.leverage ?? 1 }}x</td>
              <td class="p-3 text-right font-mono">{{ pos.quantity.toFixed(4) }}</td>
              <td class="p-3 text-right font-mono">{{ pos.entry_price.toLocaleString('en', { minimumFractionDigits: 2 }) }}</td>
              <td class="p-3 text-right font-mono">{{ livePrice(pos.symbol, pos.current_price).toLocaleString('en', { minimumFractionDigits: 2 }) }}</td>
              <td class="p-3 text-right font-mono font-semibold"
                  :class="calcPnl(pos).pnl >= 0 ? 'text-(--color-success)' : 'text-(--color-danger)'">
                <div>{{ (calcPnl(pos).pnl >= 0 ? '+' : '') + calcPnl(pos).pnl.toFixed(2) }}</div>
                <div class="text-xs font-normal">{{ (calcPnl(pos).pnlPct >= 0 ? '+' : '') + calcPnl(pos).pnlPct.toFixed(2) }}%</div>
              </td>
              <td class="p-3 text-right font-mono text-(--color-danger)">
                {{ pos.liquidation_price ? pos.liquidation_price.toLocaleString('en', { minimumFractionDigits: 2 }) : 'N/A' }}
              </td>
              <td class="p-3 text-right font-mono text-xs">
                <span v-if="pos.stop_loss" class="text-(--color-danger)">SL {{ pos.stop_loss.toLocaleString('en', { minimumFractionDigits: 2 }) }}</span>
                <span v-if="pos.stop_loss && pos.take_profit"> / </span>
                <span v-if="pos.take_profit" class="text-(--color-success)">TP {{ pos.take_profit.toLocaleString('en', { minimumFractionDigits: 2 }) }}</span>
                <span v-if="!pos.stop_loss && !pos.take_profit" class="text-(--color-text-secondary)">--</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Funding Rate -->
    <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl shadow-sm dark:shadow-none">
      <div class="p-4 border-b border-(--color-border)">
        <h3 class="font-semibold text-lg">資金費率紀錄</h3>
      </div>

      <div v-if="bot.futuresFunding.length === 0" class="p-8 text-center text-(--color-text-secondary)">
        尚無資金費率紀錄
      </div>

      <div v-else class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead>
            <tr class="text-(--color-text-secondary) border-b border-(--color-border)">
              <th class="text-left p-3">時間</th>
              <th class="text-left p-3">幣對</th>
              <th class="text-right p-3">費率</th>
              <th class="text-right p-3">費用</th>
              <th class="text-right p-3">持倉量</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="f in bot.futuresFunding" :key="f.id"
                class="border-b border-(--color-border) last:border-0">
              <td class="p-3 text-(--color-text-secondary)">{{ formatTime(f.created_at) }}</td>
              <td class="p-3 font-medium">{{ f.symbol }}</td>
              <td class="p-3 text-right font-mono"
                  :class="f.funding_rate >= 0 ? 'text-(--color-success)' : 'text-(--color-danger)'">
                {{ (f.funding_rate * 100).toFixed(4) }}%
              </td>
              <td class="p-3 text-right font-mono"
                  :class="f.funding_fee >= 0 ? 'text-(--color-success)' : 'text-(--color-danger)'">
                {{ (f.funding_fee >= 0 ? '+' : '') + f.funding_fee.toFixed(4) }}
              </td>
              <td class="p-3 text-right font-mono">{{ f.position_size.toFixed(4) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Margin Ratio History -->
    <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl shadow-sm dark:shadow-none">
      <div class="p-4 border-b border-(--color-border)">
        <h3 class="font-semibold text-lg">保證金比率歷史</h3>
      </div>

      <div v-if="marginHistory.length === 0" class="p-8 text-center text-(--color-text-secondary)">
        尚無歷史數據
      </div>

      <div v-else class="p-4 overflow-x-auto">
        <div class="flex items-end gap-1 h-32 min-w-[400px]">
          <div v-for="(m, i) in marginHistory" :key="i"
               class="flex-1 min-w-[3px] rounded-t transition-all"
               :class="m.margin_ratio >= 0.8 ? 'bg-(--color-danger)' : m.margin_ratio >= 0.5 ? 'bg-amber-500' : 'bg-(--color-success)'"
               :style="{ height: Math.max(m.margin_ratio * 100, 2) + '%' }"
               :title="`${formatTime(m.created_at)}: ${(m.margin_ratio * 100).toFixed(1)}%`" />
        </div>
        <div class="flex justify-between text-xs text-(--color-text-secondary) mt-1">
          <span v-if="marginHistory.length">{{ formatTime(marginHistory[0]!.created_at) }}</span>
          <span v-if="marginHistory.length > 1">{{ formatTime(marginHistory[marginHistory.length - 1]!.created_at) }}</span>
        </div>
      </div>
    </div>
  </div>
</template>
