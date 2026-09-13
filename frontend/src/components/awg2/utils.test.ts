import { describe, expect, it } from 'vitest'
import {
  awg2StatusMeta,
  formatAwg2ClientCount,
  formatAwg2IfacePeers,
  formatAwg2OnlineCount,
} from './utils'
import type { Awg2HealthResponse, Awg2MonitoringResponse } from '@/types'

describe('awg2StatusMeta', () => {
  it('handles null / not installed / installed', () => {
    expect(awg2StatusMeta(null).label).toBe('Нет данных')
    expect(awg2StatusMeta({ installed: false } as Awg2HealthResponse).label).toBe('Не установлен')
    expect(awg2StatusMeta({ installed: true } as Awg2HealthResponse).variant).toBe('success')
  })
})

const monitoring: Awg2MonitoringResponse = {
  ifaces: [
    { name: 'antizapret', peer_count: 3 },
    { name: 'vpn', peer_count: 0 },
  ],
  clients: [
    { name: 'alice', iface: 'antizapret', online: true, rx: 1, tx: 2 },
    { name: 'bob', iface: 'vpn', online: false, rx: 0, tx: 0 },
  ],
  stats_available: false,
}

describe('formatAwg2ClientCount', () => {
  it('counts total clients', () => {
    expect(formatAwg2ClientCount(null)).toBe('—')
    expect(formatAwg2ClientCount(monitoring)).toBe('2')
  })
})

describe('formatAwg2OnlineCount', () => {
  it('counts only online clients', () => {
    expect(formatAwg2OnlineCount(null)).toBe('—')
    expect(formatAwg2OnlineCount(monitoring)).toBe('1')
  })
})

describe('formatAwg2IfacePeers', () => {
  it('reports peer count for a known interface, dash for unknown', () => {
    expect(formatAwg2IfacePeers(monitoring, 'antizapret')).toBe('3 клиентов')
    expect(formatAwg2IfacePeers(monitoring, 'vpn')).toBe('0 клиентов')
    expect(formatAwg2IfacePeers(monitoring, 'ghost')).toBe('—')
    expect(formatAwg2IfacePeers(null, 'antizapret')).toBe('—')
  })

  it('uses singular for exactly one peer', () => {
    const single: Awg2MonitoringResponse = {
      ...monitoring,
      ifaces: [{ name: 'antizapret', peer_count: 1 }],
    }
    expect(formatAwg2IfacePeers(single, 'antizapret')).toBe('1 клиент')
  })
})
