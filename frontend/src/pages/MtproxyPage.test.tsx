// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import MtproxyPage from './MtproxyPage'

const api = vi.hoisted(() => ({
  getMtproxyStatus: vi.fn(),
  runMtproxyAction: vi.fn(),
}))

vi.mock('@/api/mtproxy', () => api)
vi.mock('@/context/NotificationContext', () => ({
  useNotifications: () => ({ success: vi.fn(), error: vi.fn() }),
}))

function user(label: string, connections: number) {
  return {
    label,
    enabled: true,
    connections,
    unique_ips: connections ? 1 : 0,
    max_conns: 20,
    max_ips: 6,
    total_bytes: 1024,
    quota_bytes: 0,
    quota_pct: null,
    expires: '0',
  }
}

describe('MtproxyPage users list', () => {
  beforeEach(() => {
    api.getMtproxyStatus.mockResolvedValue({
      nodes: [
        {
          node_id: 1,
          node_name: 'de1',
          node_online: true,
          installed: true,
          running: true,
          status: 'running',
          domain: 'cdn.example.ru',
          port: 443,
          users: [user('alice', 2), user('bob', 0)],
          availability: null,
          availability_recent: [],
        },
      ],
    })
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('is collapsed by default and expands on click', async () => {
    render(<MtproxyPage />)
    const toggle = await screen.findByRole('button', { name: /Пользователи: 2, онлайн 1/ })
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByText('alice')).toBeNull()

    fireEvent.click(toggle)
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    expect(await screen.findByText('alice')).toBeTruthy()
    // по умолчанию фильтр «онлайн»: bob без подключений не показан
    expect(screen.queryByText('bob')).toBeNull()

    fireEvent.click(screen.getByRole('tab', { name: 'Пользователи: все' }))
    expect(await screen.findByText('bob')).toBeTruthy()
  })
})
