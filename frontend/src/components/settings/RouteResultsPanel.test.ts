import { describe, expect, it } from 'vitest'
import { applyKeeneticTarget } from './RouteResultsPanel'

const SAMPLE = 'ipv6 route fd00:30::/64 fd29:7::1 auto\nipv6 route 2001:db8::/32 fd29:7::1 auto\nsystem configuration save'

describe('applyKeeneticTarget', () => {
  it('без цели оставляет файл как есть', () => {
    expect(applyKeeneticTarget(SAMPLE, '')).toBe(SAMPLE)
  })
  it('подставляет интерфейс вместо шлюза и не трогает сохранение', () => {
    expect(applyKeeneticTarget(SAMPLE, 'Wireguard0')).toBe(
      'ipv6 route fd00:30::/64 Wireguard0 auto\nipv6 route 2001:db8::/32 Wireguard0 auto\nsystem configuration save',
    )
  })
})
