import { createRouter, createWebHistory } from 'vue-router'
import { me, user } from './api'
import Home from './views/Home.vue'
import Login from './views/Login.vue'
import Settings from './views/Settings.vue'
import Shell from './views/Shell.vue'
import Tasks from './views/Tasks.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', component: Login },
    {
      path: '/',
      component: Shell,
      children: [
        { path: '', component: Home },
        { path: 'tasks', component: Tasks },
        { path: 'settings', component: Settings },
      ],
    },
  ],
})

router.beforeEach(async (to) => {
  if (!user.value) {
    try {
      await me()
    } catch {
      user.value = null
    }
  }
  if (to.path !== '/login' && !user.value) return '/login'
  if (to.path === '/login' && user.value) return '/'
})

export default router
