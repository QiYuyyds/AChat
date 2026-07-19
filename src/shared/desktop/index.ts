export {
  ENGINE_TOKEN_HEADER,
  getDesktopBridge,
  isDesktopMode,
  waitForEngineToken,
  type AchatDesktopBridge,
  type AchatDesktopWindow,
  type DesktopEngineStatus,
} from './bridge'

export {
  engineAuthHeaders,
  engineFetch,
  engineUrl,
  probeEngineHealth,
} from './engine-client'

export {
  alignLoopbackHost,
  isLoopbackHostname,
  sameLoopbackService,
  urlTargetsEngine,
} from './url'
