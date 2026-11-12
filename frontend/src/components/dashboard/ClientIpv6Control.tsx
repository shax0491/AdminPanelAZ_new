import { useEffect, useState } from 'react'
import { ApiError } from '@/api/client'
import { getClientIpv6Profiles, setClientIpv6Profile, type ClientIpv6Profile } from '@/api/ipv6'
import ClientIpv6Select from '@/components/dashboard/ClientIpv6Select'
import { Button } from '@/components/ui/button'

interface Props {
  clientName: string
  disabled?: boolean
  onSuccess: (msg: string) => void
  onError: (msg: string) => void
}

/** Профиль IPv6 существующего клиента: пишет ipv6-profile.txt и перегенерирует конфиги (WG/AWG 1.5/2.0/3.0; OpenVPN применяется при переподключении). */
export default function ClientIpv6Control({ clientName, disabled, onSuccess, onError }: Props) {
  const [saved, setSaved] = useState<ClientIpv6Profile>('default')
  const [value, setValue] = useState<ClientIpv6Profile>('default')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let alive = true
    getClientIpv6Profiles()
      .then((p) => {
        if (!alive) return
        const cur = p[clientName] ?? 'default'
        setSaved(cur)
        setValue(cur)
      })
      .catch(() => undefined)
    return () => {
      alive = false
    }
  }, [clientName])

  const apply = async () => {
    setBusy(true)
    try {
      const res = await setClientIpv6Profile(clientName, value, true)
      setSaved(value)
      onSuccess(`IPv6 «${clientName}»: ${value}${res.notes.length ? ` — ${res.notes.join('; ')}` : ''}`)
    } catch (err) {
      onError(err instanceof ApiError ? err.message : 'Не удалось сменить профиль IPv6')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
      <div className="min-w-0 flex-1">
        <ClientIpv6Select
          id={`ipv6-profile-${clientName}`}
          className="h-10"
          value={value}
          onChange={setValue}
          disabled={busy || disabled}
        />
      </div>
      <Button type="button" variant="secondary" disabled={busy || disabled || value === saved} onClick={() => void apply()}>
        {busy ? 'Применяю…' : 'Применить'}
      </Button>
    </div>
  )
}
