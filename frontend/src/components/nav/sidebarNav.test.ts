// frontend/src/components/nav/sidebarNav.test.ts
import { describe, expect, it } from 'vitest'
import {
  SIDEBAR_NAV_GROUPS,
  getVisibleSidebarNavGroups,
  isSidebarNavItemVisible,
} from './sidebarNav'

describe('SIDEBAR_NAV_GROUPS IA', () => {
  it('has role groups in order', () => {
    expect(SIDEBAR_NAV_GROUPS.map((g) => g.label)).toEqual([
      'Клиенты и доступ',
      'VPN-сеть',
      'Протоколы и обвязки',
      'Наблюдение',
      'Система',
    ])
  })

  it('names home Клиенты at /', () => {
    const home = SIDEBAR_NAV_GROUPS[0].items[0]
    expect(home).toMatchObject({ to: '/', label: 'Клиенты', end: true })
  })

  it('places settings under Система', () => {
    const panel = SIDEBAR_NAV_GROUPS.find((g) => g.label === 'Система')
    expect(panel?.items.map((i) => i.to)).toEqual(['/settings', '/telegram', '/edit-files'])
  })

  it('keeps node/proxy/routing under VPN-сеть', () => {
    const net = SIDEBAR_NAV_GROUPS.find((g) => g.label === 'VPN-сеть')
    expect(net?.items.map((i) => i.to)).toEqual(['/nodes', '/proxy', '/routing'])
  })

  it('keeps protocol tools (incl. fork MTProxy/failover) under Протоколы и обвязки', () => {
    const tools = SIDEBAR_NAV_GROUPS.find((g) => g.label === 'Протоколы и обвязки')
    expect(tools?.items.map((i) => i.to)).toEqual([
      '/antizapret',
      '/warper',
      '/warp-geo',
      '/mtproxy',
      '/failover',
    ])
  })

  it('keeps observe tools under Наблюдение', () => {
    const obs = SIDEBAR_NAV_GROUPS.find((g) => g.label === 'Наблюдение')
    expect(obs?.items.map((i) => i.to)).toEqual([
      '/monitoring',
      '/traffic',
      '/logs',
      '/server-monitor',
    ])
  })
})

describe('visibility', () => {
  it('hides adminOnly items for non-admin', () => {
    const nodes = SIDEBAR_NAV_GROUPS.flatMap((g) => g.items).find((i) => i.to === '/nodes')!
    expect(isSidebarNavItemVisible(nodes, 'user', () => true)).toBe(false)
    expect(isSidebarNavItemVisible(nodes, 'admin', () => true)).toBe(true)
  })

  it('drops empty groups when features off', () => {
    const groups = getVisibleSidebarNavGroups('admin', () => false)
    // только пункты без featureKey остаются: /settings
    expect(groups.map((g) => g.label)).toEqual(['Система'])
    expect(groups[0].items.map((i) => i.to)).toEqual(['/settings'])
  })
})
