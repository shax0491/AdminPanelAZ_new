import { describe, expect, it } from 'vitest'

import { ANTIZAPRET_WARP_OPTIONS, VPN_WARP_OPTIONS, warpModeLabel, warpModeUsesList } from './warpModes'

describe('warp modes', () => {
  it('offers exactly the values the node accepts', () => {
    expect(ANTIZAPRET_WARP_OPTIONS.map((option) => option.value)).toEqual(['1', '2', '3', '4'])
    expect(VPN_WARP_OPTIONS.map((option) => option.value)).toEqual(['1', '2'])
  })

  it('labels known values and falls back to the raw value', () => {
    expect(warpModeLabel(ANTIZAPRET_WARP_OPTIONS, '4')).toBe('Только список')
    expect(warpModeLabel(VPN_WARP_OPTIONS, '2')).toBe('Весь трафик')
    expect(warpModeLabel(ANTIZAPRET_WARP_OPTIONS, 'y')).toBe('y')
    expect(warpModeLabel(ANTIZAPRET_WARP_OPTIONS, undefined)).toBe('—')
  })

  it('knows which modes depend on the WARP host list', () => {
    expect(warpModeUsesList('3')).toBe(true)
    expect(warpModeUsesList('4')).toBe(true)
    expect(warpModeUsesList('1')).toBe(false)
    expect(warpModeUsesList('2')).toBe(false)
  })
})
