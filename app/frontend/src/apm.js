/**
 * Elastic APM RUM (Real User Monitoring) Configuration
 * Captures browser performance metrics and frontend errors
 */

import { init as initApm } from '@elastic/apm-rum'

const apm = initApm({
  // Service name and version
  serviceName: 'frontend',
  serviceVersion: '1.0.0',

  // APM Server Configuration
  serverUrl: window.location.protocol + '//' + window.location.hostname + ':8200',

  // Environment
  environment: 'docker',

  // Sampling Configuration
  transactionSampleRate: 1.0, // 100% sampling for visibility

  // Client Side Configuration
  centralConfig: false,
  disableInstrumentations: [],

  // Log Configuration
  logLevel: 'info',

  // Error Configuration
  captureExceptionDetails: true,

  // Page Load Configuration
  pageLoadTransactionName: 'page-load',

  // Custom Configuration
  globalLabels: {
    environment: 'docker',
    application: 'todos-app',
    cluster: 'docker-local'
  }
})

// Capture unhandled errors
window.addEventListener('error', function (event) {
  apm.captureError(event.error || event.message)
})

// Capture unhandled promise rejections
window.addEventListener('unhandledrejection', function (event) {
  apm.captureError(event.reason)
})

export default apm
