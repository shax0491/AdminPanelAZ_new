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

import { describeModeSwitch, isScopePending } from './warpModes'

describe('mode switch confirmation', () => {
  it('describes the transition with readable labels', () => {
    expect(describeModeSwitch('AntiZapret VPN', ANTIZAPRET_WARP_OPTIONS, '2', '4')).toEqual({
      title: 'Режим WARP: AntiZapret VPN',
      summary: 'Весь трафик → Только список',
    })
    expect(describeModeSwitch('Полный VPN', VPN_WARP_OPTIONS, undefined, '1').summary).toBe('— → Выключен')
  })

  it('knows which scope still waits for up.sh', () => {
    expect(isScopePending(['antizapret'], 'antizapret')).toBe(true)
    expect(isScopePending(['antizapret'], 'vpn')).toBe(false)
    expect(isScopePending(undefined, 'vpn')).toBe(false)
    expect(isScopePending([], 'antizapret')).toBe(false)
  })
})
