// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  activateNodeReverse,
  getNodeReverseStatus,
  provisionNodeReverse,
} from '@/api/client';
import type { Node, ReverseTunnelLink } from '@/types';
import { NodeReverseTunnelControls } from './NodeReverseTunnelControls';

vi.mock('@/api/client', () => ({
  getNodeReverseStatus: vi.fn(),
  provisionNodeReverse: vi.fn(),
  activateNodeReverse: vi.fn(),
}));

const mockedStatus = vi.mocked(getNodeReverseStatus);
const mockedProvision = vi.mocked(provisionNodeReverse);
const mockedActivate = vi.mocked(activateNodeReverse);

const proxyLinked = { id: 3, node_kind: 'proxy', linked_vpn_node_id: 9 } as unknown as Node;
const vpnNode = { id: 4, node_kind: 'vpn', linked_vpn_node_id: null } as unknown as Node;
const proxyUnlinked = { id: 5, node_kind: 'proxy', linked_vpn_node_id: null } as unknown as Node;

const linkFixture: ReverseTunnelLink = {
  id: 1,
  proxy_node_id: 3,
  exit_node_id: 9,
  listen_port: 4500,
  tunnel_ipv4: '10.66.0.1',
  tunnel_ipv6: '',
  relay_public_key: 'RELAYPUBKEY',
  exit_public_key: 'EXITPUBKEY',
  state: 'provisioned',
  last_error: null,
  created_at: '2026-11-10T00:00:00Z',
  updated_at: '2026-11-10T00:00:00Z',
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('NodeReverseTunnelControls', () => {
  it('не рендерится для vpn-узла и proxy без связи', () => {
    const a = render(<NodeReverseTunnelControls node={vpnNode} onUpdated={vi.fn()} />);
    expect(a.container.innerHTML).toBe("");
    cleanup();
    const b = render(<NodeReverseTunnelControls node={proxyUnlinked} onUpdated={vi.fn()} />);
    expect(b.container.innerHTML).toBe("");
    expect(mockedStatus).not.toHaveBeenCalled();
  });

  it('404 (null) показывает пустое состояние и кнопку «Подготовить»', async () => {
    mockedStatus.mockResolvedValue(null);
    render(<NodeReverseTunnelControls node={proxyLinked} onUpdated={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /обновить/i }));
    expect(await screen.findByText(/Reverse Tunnel не настроен/i)).toBeTruthy();
    expect(screen.getByRole('button', { name: /подготовить/i })).toHaveProperty("disabled", false);
  });

  it('provision вызывает API, показывает порт и ключи, зовёт onUpdated', async () => {
    mockedStatus.mockResolvedValue(null);
    mockedProvision.mockResolvedValue(linkFixture);
    const onUpdated = vi.fn();
    render(<NodeReverseTunnelControls node={proxyLinked} onUpdated={onUpdated} />);
    fireEvent.click(screen.getByRole('button', { name: /подготовить/i }));
    expect(await screen.findByText('4500')).toBeTruthy();
    expect(screen.getByText('RELAYPUBKEY')).toBeTruthy();
    expect(screen.getByText('EXITPUBKEY')).toBeTruthy();
    expect(mockedProvision).toHaveBeenCalledWith(3);
    await waitFor(() => expect(onUpdated).toHaveBeenCalled());
  });

  it('activate отменён через confirm → API не вызван', async () => {
    mockedStatus.mockResolvedValue({
      link_id: 1,
      state: 'provisioned',
      exit: { connected: true, interface: 'wg0' },
      watchdog: { last_run: 'ok' },
    });
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    render(<NodeReverseTunnelControls node={proxyLinked} onUpdated={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /обновить/i }));
    expect(await screen.findByText(/connected/i)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /активировать/i }));
    expect(mockedActivate).not.toHaveBeenCalled();
    expect(confirmSpy).toHaveBeenCalled();
  });

  it('activate подтверждён → API вызван, состояние обновлено', async () => {
    mockedStatus.mockResolvedValue({
      link_id: 1,
      state: 'provisioned',
      exit: { connected: true, interface: 'wg0' },
      watchdog: {},
    });
    mockedActivate.mockResolvedValue({ ...linkFixture, state: 'active' });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<NodeReverseTunnelControls node={proxyLinked} onUpdated={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /активировать/i }));
    await waitFor(() => expect(mockedActivate).toHaveBeenCalledWith(3));
    expect(await screen.findByText('active')).toBeTruthy();
  });

  it('ошибка API отображается через role=alert', async () => {
    mockedStatus.mockRejectedValue(new Error('agent offline'));
    render(<NodeReverseTunnelControls node={proxyLinked} onUpdated={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /обновить/i }));
    expect((await screen.findByRole('alert')).textContent).toContain('agent offline');
  });
});
