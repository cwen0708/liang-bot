import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'dashboard',
      component: () => import('@/pages/DashboardPage.vue'),
    },
    {
      path: '/trading',
      name: 'trading',
      component: () => import('@/pages/TradingPage.vue'),
    },
    {
      path: '/orders',
      name: 'orders',
      component: () => import('@/pages/OrdersPage.vue'),
    },
    {
      path: '/strategy',
      name: 'strategy',
      component: () => import('@/pages/StrategyPage.vue'),
    },
    {
      path: '/loan-guard',
      name: 'loan-guard',
      component: () => import('@/pages/LoanGuardPage.vue'),
    },
    {
      path: '/config',
      name: 'config',
      component: () => import('@/pages/ConfigPage.vue'),
    },
    {
      path: '/logs',
      name: 'logs',
      component: () => import('@/pages/LogsPage.vue'),
    },
  ],
})

export default router
