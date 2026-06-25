import { describe, expect, it } from 'vitest'

import { formatMermaidError, normaliseMermaidSource } from './mermaid-normalize'

describe('normaliseMermaidSource', () => {
  it('quotes flowchart labels that contain Chinese and math syntax', () => {
    const result = normaliseMermaidSource(`
\`\`\`mermaid
flowchart LR
  A[研究背景: 堆叠智能超表面SIM]
  subgraph B[传统简化SIM模型]
    C21[全局矩阵求逆复杂度 从O(LN)^3 降至 O(L*N^2)]
  end
  A --> B
\`\`\`
`)

    expect(result).toEqual({
      ok: true,
      source: [
        'flowchart LR',
        '  A["研究背景: 堆叠智能超表面SIM"]',
        '  subgraph B["传统简化SIM模型"]',
        '    C21["全局矩阵求逆复杂度 从O(LN)^3 降至 O(L*N^2)"]',
        '  end',
        '  A --> B',
      ].join('\n'),
    })
  })

  it('rejects style lines with trailing prose', () => {
    const result = normaliseMermaidSource(`
flowchart LR
  A["开始"]
  style A fill:#1A3C6E,color:#fff 比如这个就出错了
`)

    expect(result).toEqual({
      ok: false,
      error:
        'Line 3: invalid style syntax. Use "style ID fill:#hex,color:#hex" without trailing prose.\nstyle A fill:#1A3C6E,color:#fff 比如这个就出错了',
    })
  })

  it('formats parser errors into readable lines', () => {
    expect(formatMermaidError('Parse error Expecting NODE_STRING got EOF')).toBe(
      'Parse error\nExpecting NODE_STRING\ngot EOF',
    )
  })
})
