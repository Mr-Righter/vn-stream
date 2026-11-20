import { Button } from 'antd';
import type { ReactNode } from 'react';
import { t } from './preferences-store.js';
import { PreferenceControls } from './preferences.js';

export function AppHeader({ onHome, children }: { onHome?: () => void; children?: ReactNode }) {
  const brand = (
    <span className="vn-brand-content">
      <img src="/app-icons/icon-192.png" width={28} height={28} alt="" />
      <span className="vn-brand-name">VN Stream</span>
    </span>
  );
  return (
    <header className="vn-app-header">
      <div className="vn-app-header-inner">
        {onHome ? (
          <Button className="vn-brand" type="text" title={t('ui.backToLibrary')} onClick={onHome}>
            {brand}
          </Button>
        ) : (
          <div className="vn-brand">{brand}</div>
        )}
        <div className="vn-top-controls" id="top-controls">
          <PreferenceControls />
          {children}
        </div>
      </div>
    </header>
  );
}
