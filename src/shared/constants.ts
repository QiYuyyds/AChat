/**
 * 跨 server / client 共享的常量。放在 `shared/` 下避免 client 误 import server 文件。
 */

/** 单会话内 pin 消息上限；超过由 conversation-service 拒绝以防 system prompt 膨胀。 */
export const PIN_LIMIT_PER_CONVERSATION = 5
