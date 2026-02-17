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
      path: '/review',
      name: 'review',
      component: () => import('@/pages/ReviewPage.vue'),
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
      path: '/futures',
      name: 'futures',
      component: () => import('@/pages/FuturesPage.vue'),
    },
    {
      path: '/logs',
      name: 'logs',
      component: () => import('@/pages/LogsPage.vue'),
    },
    {
      path: '/tx',
      name: 'tx',
      component: () => import('@/pages/TXPage.vue'),
    },
    {
      path: '/more',
      name: 'more',
      component: () => import('@/pages/MorePage.vue'),
    },
  ],
})

export default router
