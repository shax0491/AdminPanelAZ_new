import { useCallback, useEffect, useState } from 'react'
import { ApiError, applyIpv6, getIpv6Status } from '@/api/client'
import { ConfirmDialogHost } from '@/components/shared/ConfirmDialog'
import SettingsAlert from '@/components/settings/SettingsAlert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useNotifications } from '@/context/NotificationContext'
import { useConfirmDialog } from '@/hooks/useConfirmDialog'
import type { Ipv6Status } from '@/types'

type Ipv6StatusCardProps = {
  activeNodeId: number | null
  disabled?: boolean
}

const MODE_LABEL: Record<string, string> = {
  a: 'a — только IPv4',
  b: 'b — полный VPN с IPv6, split без IPv6',
  c: 'c — b + Fake-IPv6 для заблокированных доменов',
}

export default function Ipv6StatusCard({ activeNodeId, disabled = false }: Ipv6StatusCardProps) {
  const { success, error: notifyError } = useNotifications()
  const { confirm, dialogProps } = useConfirmDialog()
  const [state, setState] = useState<Ipv6Status | null>(null)
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setState(null)
    setLoadError(null)
    if (activeNodeId == null) return
    setLoading(true)
    try {
      setState(await getIpv6Status())
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : 'Не удалось прочитать состояние IPv6')
    } finally {
      setLoading(false)
    }
  }, [activeNodeId])

  useEffect(() => {
    void load()
  }, [load])

  function askApply() {
    confirm({
      title: 'Применить настройки IPv6?',
      description:
        'antizapret перезапустится (несколько секунд VPN-клиенты без связи): up.sh заново проверит выход IPv6 и выставит режим. Если выход не отвечает, режим сам вернётся на a.',
      confirmLabel: 'Применить',
      onConfirm: async () => {
        try {
          setState(await applyIpv6())
          success('IPv6 применён')
        } catch (err) {
          notifyError(err instanceof ApiError ? err.message : 'Не удалось применить IPv6')
        }
      },
    })
  }

  const fellBack = state != null && state.enabled && state.requested_mode !== state.effective_mode

  return (
    <Card className="overflow-hidden">
      <CardHeader className="pb-3">
        <CardTitle className="text-base">IPv6</CardTitle>
        <CardDescription className="mt-1">
          Состояние IPv6 узла. Режим, выход и профиль по умолчанию задаются в «Конфиг AntiZapret» (ENABLE_IPV6,
          IPV6_MODE, IPV6_EGRESS, IPV6_PROFILE_DEFAULT), списки подсетей и профили клиентов — в Редакторе файлов.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 border-t px-4 py-4 sm:px-5">
        {activeNodeId == null && <p className="text-xs text-muted-foreground">Выберите VPN-узел вверху.</p>}
        {loadError && activeNodeId != null && (
          <SettingsAlert variant="danger" title="Не удалось прочитать состояние IPv6">
            {loadError}
          </SettingsAlert>
        )}
        {state && (
          <>
            {fellBack && (
              <SettingsAlert variant="danger" title="IPv6 откатился на режим a">
                {state.reason || 'выход IPv6 не отвечает'}
              </SettingsAlert>
            )}
            <dl className="grid grid-cols-[auto,1fr] gap-x-4 gap-y-1 text-sm">
              <dt className="text-muted-foreground">IPv6 включён</dt>
              <dd>{state.enabled ? 'да' : 'нет'}</dd>
              <dt className="text-muted-foreground">Режим (задан / действует)</dt>
              <dd>
                {state.requested_mode} / {MODE_LABEL[state.effective_mode] ?? state.effective_mode}
              </dd>
              <dt className="text-muted-foreground">Выход</dt>
              <dd>
                {state.egress}
                {state.egress_interface ? ` (${state.egress_interface})` : ''}
              </dd>
              <dt className="text-muted-foreground">Нативный IPv6-маршрут узла</dt>
              <dd>{state.native_default_route ? 'есть' : 'нет'}</dd>
              <dt className="text-muted-foreground">Профиль по умолчанию</dt>
              <dd>{state.profile_default}</dd>
              <dt className="text-muted-foreground">Fake-IPv6 в карте</dt>
              <dd>{state.fake6_mappings}</dd>
            </dl>
          </>
        )}
        {activeNodeId != null && (
          <div className="flex gap-2">
            <Button type="button" variant="outline" size="sm" disabled={disabled || loading} onClick={() => void load()}>
              Обновить
            </Button>
            <Button type="button" size="sm" disabled={disabled || loading || state == null} onClick={askApply}>
              Применить (перезапуск antizapret)
            </Button>
          </div>
        )}
      </CardContent>
      <ConfirmDialogHost dialogProps={dialogProps} />
    </Card>
  )
}
