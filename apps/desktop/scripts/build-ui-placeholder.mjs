/** Internal smoke only: allow packaging the lightweight placeholder UI. */
process.env.ACHAT_UI_ALLOW_PLACEHOLDER = '1'
await import('./build-ui.mjs')
