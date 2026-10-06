import { describe, expect, it } from 'vitest'

import { touchesWarpSettings, WARP_SETTING_KEYS } from './warpSettings'

describe('touchesWarpSettings', () => {
  it('asks to apply when a WARP mode, protection or MTU key changed', () => {
    for (const key of WARP_SETTING_KEYS) {
      expect(touchesWarpSettings(['OPENVPN_PATCH', key])).toBe(true)
    }
  })

  it('stays quiet for unrelated settings and for an empty save', () => {
    expect(touchesWarpSettings(['OPENVPN_PATCH', 'ANTIZAPRET_ADBLOCK'])).toBe(false)
    expect(touchesWarpSettings([])).toBe(false)
  })
})
