// The Vue build version to load with the `import` command
// (runtime-only or standalone) has been set in webpack.base.conf with an alias.

// [ADDED] Initialize Elastic APM RUM FIRST
// eslint-disable-next-line no-unused-vars
import apm from './apm'

import Vue from 'vue'
import BootstrapVue from 'bootstrap-vue/dist/bootstrap-vue.esm'
import 'bootstrap/dist/css/bootstrap.css'
import 'bootstrap-vue/dist/bootstrap-vue.css'
Vue.use(BootstrapVue)

import VueResource from 'vue-resource'
Vue.use(VueResource)

import App from '@/components/App'
import router from './router'
import store from './store'

Vue.config.productionTip = false

/* Auth plugin */
import Auth from './auth'
Vue.use(Auth)

/* Auth plugin */
import Zipkin from './zipkin'
Vue.use(Zipkin)

/* eslint-disable no-new */
new Vue({
  el: '#app',
  router,
  store,
  template: '<App/>',
  components: { App }
})
