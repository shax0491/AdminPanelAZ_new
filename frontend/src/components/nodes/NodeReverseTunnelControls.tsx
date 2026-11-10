import { useEffect, useRef, useState } from 'react';
import { Cable, Check, Copy, RefreshCw } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  activateNodeReverse,
  getNodeReverseStatus,
  provisionNodeReverse,
} from '@/api/client';
import type { Node, ReverseTunnelLink, ReverseTunnelStatus } from '@/types';

function KV({ title, data }: { title: string; data: Record<string, unknown> | null | undefined }) {
  const entries = Object.entries(data ?? {});
  if (entries.length === 0) return null;
  return (
    <div>
      <h4 className="mb-1 text-sm font-medium">{title}</h4>
      <dl className="space-y-1 text-sm">
        {entries.map(([key, value]) => (
          <div key={key} className="flex flex-wrap gap-2">
            <dt className="text-muted-foreground">{key}</dt>
            <dd className="font-mono break-all">
              {value !== null && typeof value === 'object' ? JSON.stringify(value) : String(value)}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function KeyRow({
  label,
  value,
  copied,
  onCopy,
}: {
  label: string;
  value: string | null | undefined;
  copied: boolean;
  onCopy: () => void;
}) {
  return (
    <div>
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm text-muted-foreground">{label}</span>
        <Button type="button" variant="ghost" size="sm" onClick={onCopy} disabled={!value}>
          {copied ? (
            <Check className="mr-1 h-3.5 w-3.5" aria-hidden />
          ) : (
            <Copy className="mr-1 h-3.5 w-3.5" aria-hidden />
          )}
          {copied ? 'Скопировано' : 'Копировать'}
        </Button>
      </div>
      <code className="block break-all rounded bg-muted p-2 font-mono text-xs">
        {value || '—'}
      </code>
    </div>
  );
}

export function NodeReverseTunnelControls({ node, onUpdated }: { node: Node; onUpdated: () => void }) {
  const [status, setStatus] = useState<ReverseTunnelStatus | null>(null);
  const [link, setLink] = useState<ReverseTunnelLink | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  const ignoreRef = useRef(false);

  useEffect(() => {
    return () => {
      ignoreRef.current = true;
    };
  }, []);

  if (node.node_kind !== 'proxy' || node.linked_vpn_node_id == null) return null;

  async function refresh() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await getNodeReverseStatus(node.id);
      if (ignoreRef.current) return;
      setStatus(result);
      setLoaded(true);
    } catch (e) {
      if (ignoreRef.current) return;
      setError(e instanceof Error ? e.message : 'Не удалось загрузить статус Reverse Tunnel');
    } finally {
      if (!ignoreRef.current) setBusy(false);
    }
  }

  async function run(action: () => Promise<ReverseTunnelLink>) {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await action();
      if (ignoreRef.current) return;
      setLink(result);
      onUpdated();
    } catch (e) {
      if (ignoreRef.current) return;
      setError(e instanceof Error ? e.message : 'Операция не выполнена');
    } finally {
      if (!ignoreRef.current) setBusy(false);
    }
  }

  async function activate() {
    if (busy) return;
    if (!window.confirm('Активация переключит транспорт узла на relay. Продолжить?')) return;
    await run(() => activateNodeReverse(node.id));
  }

  async function copyKey(kind: string, value: string | null | undefined) {
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
      setCopiedKey(kind);
    } catch {
      setError('Не удалось скопировать ключ в буфер обмена');
    }
  }

  return (
    <div className="rounded-md border p-3 space-y-3" data-testid="node-reverse-tunnel">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2">
          <Cable className="h-4 w-4" aria-hidden />
          <span className="font-medium">Reverse Tunnel</span>
          {loaded && status && <Badge variant="secondary">state: {String(status.state)}</Badge>}
          {loaded && !status && <Badge variant="outline">не настроен</Badge>}
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => void refresh()}
            disabled={busy}
          >
            <RefreshCw className={`mr-1 h-4 w-4 ${busy ? 'animate-spin' : ''}`} aria-hidden />
            Обновить
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => void run(() => provisionNodeReverse(node.id))}
            disabled={busy}
          >
            Подготовить
          </Button>
          <Button type="button" size="sm" onClick={() => void activate()} disabled={busy}>
            Активировать
          </Button>
        </div>
      </div>

      {error && (
        <p role="alert" className="text-sm text-red-600">{error}</p>
      )}

      {!loaded && !busy && !error && (
        <p className="text-sm text-muted-foreground">
          Статус не загружен. Нажмите «Обновить», чтобы проверить связь с exit-узлом.
        </p>
      )}

      {loaded && !status && !link && (
        <p className="text-sm text-muted-foreground">
          Reverse Tunnel не настроен. Нажмите «Подготовить», чтобы создать связь с exit-узлом.
        </p>
      )}

      {status && (
        <div className="space-y-2">
          <KV title="Exit" data={status.exit} />
          <KV title="Watchdog" data={status.watchdog} />
        </div>
      )}

      {link && (
        <div className="space-y-2">
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
            <dt className="text-muted-foreground">Состояние связи</dt>
            <dd>{link.state}</dd>
            <dt className="text-muted-foreground">Порт прослушивания</dt>
            <dd className="font-mono">{link.listen_port}</dd>
            <dt className="text-muted-foreground">Tunnel IPv4</dt>
            <dd className="font-mono break-all">{link.tunnel_ipv4 || '—'}</dd>
            <dt className="text-muted-foreground">Tunnel IPv6</dt>
            <dd className="font-mono break-all">{link.tunnel_ipv6 || '—'}</dd>
          </dl>
          <KeyRow
            label="Публичный ключ relay"
            value={link.relay_public_key}
            copied={copiedKey === 'relay'}
            onCopy={() => void copyKey('relay', link.relay_public_key)}
          />
          <KeyRow
            label="Публичный ключ exit"
            value={link.exit_public_key}
            copied={copiedKey === 'exit'}
            onCopy={() => void copyKey('exit', link.exit_public_key)}
          />
          {link.last_error && (
            <p role="alert" className="text-sm text-red-600">{link.last_error}</p>
          )}
        </div>
      )}
    </div>
  );
}
