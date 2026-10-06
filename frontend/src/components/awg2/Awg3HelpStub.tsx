import { Info } from 'lucide-react'

export default function Awg3HelpStub() {
  return (
    <div className="space-y-4 rounded-xl border bg-card/50 p-5">
      <div className="flex items-start gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
          <Info className="h-4 w-4" />
        </div>
        <div className="min-w-0 space-y-2 text-sm">
          <h2 className="text-base font-semibold tracking-tight">Справка</h2>
          <p className="text-muted-foreground">
            AmneziaWG 3.1 — отдельный интерфейс <code className="text-xs">awg1</code> поверх AntiZapret
            (userspace <code className="text-xs">amneziawg-go</code>, UDP 51821). Не влияет на AmneziaWG 2.0
            и 1.5. Штатные OpenVPN и WireGuard не затрагиваются.
          </p>
          <div className="space-y-1.5 text-muted-foreground">
            <p>
              <span className="font-medium text-foreground">Клиенты:</span> создание, скачивание и
              блокировка — на странице <strong className="text-foreground">Клиенты</strong> (галочка «AmneziaWG 3.1»,
              режим «антизапрет» или «полный VPN»). Здесь только статус и живой мониторинг пиров.
            </p>
            <p>
              <span className="font-medium text-foreground">Защита транспорта (Transport Protection):</span>{' '}
              на сервере включены <code className="text-xs">ContentPaddingAddition = 2</code>,{' '}
              <code className="text-xs">RandomTrailers = on</code> и <code className="text-xs">DisableCookies = on</code>.
              Ключ <code className="text-xs">HeaderProtectionKey</code> общий для сервера и клиентов и генерируется
              один раз при установке. Клиентский конфиг получает все четыре параметра с сервера.
            </p>
            <p>
              <span className="font-medium text-foreground">MTU:</span> клиентские профили AmneziaWG 3.1 получают{' '}
              <code className="text-xs">MTU = 1280</code>. Пакеты с паддингом становятся больше, при большем MTU
              соединение фрагментируется и обрывается.
            </p>
            <p>
              <span className="font-medium text-foreground">Обновление узла:</span> параметры защиты транспорта
              дописываются в <code className="text-xs">awg1.conf</code> автоматически при старте агента узла, служба{' '}
              <code className="text-xs">awg3@awg1</code> перезапускается, если файл изменился.
            </p>
            <p>
              <span className="font-medium text-foreground">Статистика:</span> живые пиры читаются из{' '}
              <code className="text-xs">awg show awg1 dump</code>; накопленный RX/TX и лимиты — в{' '}
              <strong className="text-foreground">Мониторинг трафика</strong> (протокол AmneziaWG 3.1). Лимит
              трафика для AmneziaWG 3.1 пока не поддержан.
            </p>
            <p>
              <span className="font-medium text-foreground">HA и failover:</span> при репликации копируются слой{' '}
              <code className="text-xs">awg1</code> (ключи, <code className="text-xs">awg1.conf</code>, реестр клиентов с
              флагами блокировок) и перезапускается <code className="text-xs">awg3@awg1</code> на replica.
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
