import { Info } from 'lucide-react'

export default function Awg2HelpStub() {
  return (
    <div className="space-y-4 rounded-xl border bg-card/50 p-5">
      <div className="flex items-start gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
          <Info className="h-4 w-4" />
        </div>
        <div className="min-w-0 space-y-2 text-sm">
          <h2 className="text-base font-semibold tracking-tight">Справка</h2>
          <p className="text-muted-foreground">
            Нативный AmneziaWG 2.0 — второй туннель поверх AntiZapret, встроенный прямо в{' '}
            <code className="text-xs">setup.sh</code> (собирается вместе с остальным VPN, отдельно
            ничего ставить не нужно). Штатные OpenVPN и WireGuard/AmneziaWG 1.5 не затрагиваются.
          </p>
          <div className="space-y-1.5 text-muted-foreground">
            <p>
              <span className="font-medium text-foreground">Клиенты:</span> создание, скачивание и
              блокировка — на странице{' '}
              <strong className="text-foreground">Клиенты</strong> (галочка «AmneziaWG 2.0»).
              Отдельной вкладки клиентов на <code className="text-xs">/awg2</code> нет — здесь только
              статус и живой мониторинг пиров.
            </p>
            <p>
              <span className="font-medium text-foreground">Обфускация (Jc/Jmin/Jmax/S1-S4/H1-H4):</span>{' '}
              задаётся один раз при установке <code className="text-xs">setup.sh</code> и читается
              с живого серверного интерфейса при каждой генерации клиента — панель не меняет её на
              лету, поскольку это потребовало бы перезапуска интерфейса и разрыва уже подключённых
              клиентов.
            </p>
            <p>
              <span className="font-medium text-foreground">MTU:</span> клиентские профили AmneziaWG 2.0
              всегда получают <code className="text-xs">MTU = 1280</code> — минимум, гарантированно
              проходящий через мобильные сети и CGNAT.
            </p>
            <p>
              <span className="font-medium text-foreground">Статистика:</span> живые пиры (эта
              страница, «Мониторинг») читаются напрямую из{' '}
              <code className="text-xs">awg show &lt;iface&gt; dump</code>; накопленный RX/TX и лимиты
              — в <strong className="text-foreground">Мониторинг трафика</strong> (протокол AmneziaWG 2.0).
            </p>
            <p>
              <span className="font-medium text-foreground">HA:</span> при репликации на replica-узел
              копируются серверные конфиги, ключ и архив клиентских профилей, затем применяется{' '}
              <code className="text-xs">awg syncconf</code> (только диф пиров, без перезапуска
              интерфейса).
            </p>
            <p>
              <span className="font-medium text-foreground">Установка/переустановка:</span> выполняется
              только через <code className="text-xs">setup.sh</code> по SSH на сервере — из панели
              недоступна, поскольку это часть базового VPN-стека, а не отдельный компонент.
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
