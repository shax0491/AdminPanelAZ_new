import { describe, expect, it } from 'vitest'
import type { Ipv6Status } from '@/types'
import { ipv6BadgeInfo } from './Ipv6Badge'

const base: Ipv6Status = {
  enabled: true,
  requested_mode: 'c',
  effective_mode: 'c',
  egress: 'native',
  active_egress: 'native',
  egress_interface: 'ens3',
  profile_default: 'dual',
  reason: '',
  native_default_route: true,
  fake6_mappings: 0,
}

describe('ipv6BadgeInfo', () => {
  it('Dual-Stack с названием выхода', () => {
    expect(ipv6BadgeInfo({ ...base, active_egress: 'warp' })).toEqual({ tone: 'ok', text: 'IPv6: Dual-Stack (WARP)' })
  })
  it('режим a без запроса IPv6 - защита от утечек', () => {
    expect(ipv6BadgeInfo({ ...base, enabled: false, requested_mode: 'a', effective_mode: 'a' })?.tone).toBe('safe')
  })
  it('запрошен IPv6, но up.sh откатил на a - ошибка выхода', () => {
    expect(ipv6BadgeInfo({ ...base, effective_mode: 'a', reason: 'не отвечает' })?.tone).toBe('error')
  })
  it('нет данных - нет бейджа', () => {
    expect(ipv6BadgeInfo(null)).toBeNull()
  })
})
