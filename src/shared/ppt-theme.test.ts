import { describe, expect, it } from 'vitest'

import { DEFAULT_PPT_THEME, detectBulletTone, resolvePptTheme } from './ppt-theme'

describe('resolvePptTheme', () => {
  it('fills all tokens with defaults when theme is undefined', () => {
    expect(resolvePptTheme(undefined)).toEqual(DEFAULT_PPT_THEME)
  })

  it('keeps provided tokens, defaults the rest', () => {
    const r = resolvePptTheme({ primary: '1A3C6E', background: 'F8F9FA' })
    expect(r.primary).toBe('1A3C6E')
    expect(r.background).toBe('F8F9FA')
    expect(r.textBody).toBe(DEFAULT_PPT_THEME.textBody)
    expect(r.accentPositive).toBe(DEFAULT_PPT_THEME.accentPositive)
  })

  it('strips leading # and maps legacy primaryColor/fontFace', () => {
    const r = resolvePptTheme({ primaryColor: '#123ABC', fontFace: 'Georgia' })
    expect(r.primary).toBe('123ABC')
    expect(r.fontHeading).toBe('Georgia')
    expect(r.fontBody).toBe('Georgia')
  })

  it('prefers new fields over legacy', () => {
    expect(resolvePptTheme({ primary: 'AAAAAA', primaryColor: 'BBBBBB' }).primary).toBe('AAAAAA')
  })
})

describe('detectBulletTone', () => {
  it('negative wins on warning/risk/churn (even with +N)', () => {
    expect(detectBulletTone('⚠ Sales cycle +6 days, slower pipeline')).toBe('negative')
    expect(detectBulletTone('SMB churn uptick 4.2% → 5.8%')).toBe('negative')
    expect(detectBulletTone('风险：交付延迟')).toBe('negative')
  })

  it('positive on growth / improved / +N% / 🏆', () => {
    expect(detectBulletTone('Revenue +22% YoY')).toBe('positive')
    expect(detectBulletTone('NPS improved 42 → 51')).toBe('positive')
    expect(detectBulletTone('🏆 Closed $1.2M enterprise deal')).toBe('positive')
  })

  it('neutral when no signal', () => {
    expect(detectBulletTone('Gross margin held steady at 74%')).toBe('neutral')
    expect(detectBulletTone('Agenda · Q&A')).toBe('neutral')
  })
})
