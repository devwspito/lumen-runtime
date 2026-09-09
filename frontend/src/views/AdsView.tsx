/**
 * AdsView — "Anuncios": embeds the Safent Ads panel (its own React SPA) so
 * campaign work happens without leaving Safent. The panel origin comes from
 * the owner-set safent-ads managed-remote MCP URL (see McpView) — never
 * built from free text. Only reachable via the sidebar entry, which the
 * Layout hides until that endpoint exists.
 */
import { ExternalLink, Link2, Lightbulb, Megaphone } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { useT } from '../lib/i18n'
import { useAdsPanelOrigin } from '../hooks/useAdsPanel'
import { PageHeader } from '../components/ui/PageHeader'
import { EmptyState } from '../components/ui/EmptyState'
import { Button } from '../components/ui/Button'
import css from './AdsView.module.css'

interface AdsQuickLinksProps {
  origin: string
}

function AdsQuickLinks({ origin }: AdsQuickLinksProps) {
  const t = useT()
  return (
    <div className={css.quickLinks}>
      <a className="cv-btn cv-btn--secondary cv-btn--sm" href={origin} target="_blank" rel="noopener noreferrer">
        <ExternalLink size={13} aria-hidden="true" />
        {t('ads.panel.open')}
      </a>
      <a className="cv-btn cv-btn--secondary cv-btn--sm" href={`${origin}/conexiones`} target="_blank" rel="noopener noreferrer">
        <Link2 size={13} aria-hidden="true" />
        {t('ads.panel.connections')}
      </a>
      <a className="cv-btn cv-btn--secondary cv-btn--sm" href={`${origin}/propuestas`} target="_blank" rel="noopener noreferrer">
        <Lightbulb size={13} aria-hidden="true" />
        {t('ads.panel.proposals')}
      </a>
    </div>
  )
}

export default function AdsView() {
  const t = useT()
  const navigate = useNavigate()
  const origin = useAdsPanelOrigin()

  if (!origin) {
    return (
      <>
        <PageHeader title={t('nav.ads')} subtitle={t('ads.subtitle')} />
        <div className="view-body">
          <EmptyState
            icon={<Megaphone size={28} />}
            title={t('ads.empty.title')}
            description={t('ads.empty.desc')}
            action={
              <Button variant="primary" size="sm" onClick={() => navigate('/capacidades?tab=mcp')}>
                {t('ads.empty.cta')}
              </Button>
            }
          />
        </div>
      </>
    )
  }

  return (
    <>
      <PageHeader title={t('nav.ads')} subtitle={t('ads.subtitle')} actions={<AdsQuickLinks origin={origin} />} />
      <div className="view-body">
        <p className={css.hint}>{t('ads.panel.hint')}</p>

        <div className={css.frameWrap}>
          <iframe
            key={origin}
            src={origin}
            title={t('ads.iframe.title')}
            className={css.frame}
            sandbox="allow-same-origin allow-scripts allow-forms allow-popups"
          />
        </div>

        {/* X-Frame-Options / frame-ancestors blocks a cross-origin iframe silently
            (no load/error event we can key off) — always offer the escape hatch
            rather than trying to detect a block that may never fire a JS event. */}
        <p className={css.fallback}>
          {t('ads.iframe.fallback')}{' '}
          <a href={origin} target="_blank" rel="noopener noreferrer">
            {t('ads.panel.open')}
          </a>
        </p>
      </div>
    </>
  )
}
