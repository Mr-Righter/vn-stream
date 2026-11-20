import { Button, Dropdown } from 'antd';
import { useRef, useState } from 'react';
import { ChevronDown, Languages } from 'lucide-react';
import { setLocale, setTheme, t } from './preferences-store.js';
import { usePreferences } from './provider.js';

export function PreferenceControls() {
  const { language, appearance } = usePreferences();
  const languageButton = useRef<HTMLButtonElement>(null);
  const [languageOpen, setLanguageOpen] = useState(false);
  const isDark = appearance === 'dark';
  return (
    <div className="vn-preferences">
      <Dropdown
        trigger={['click']}
        placement="bottomRight"
        autoFocus
        onOpenChange={setLanguageOpen}
        menu={{
          selectable: true,
          selectedKeys: [language],
          items: ['zh', 'en'].map((value) => ({
            key: value,
            label: <span id={`language-${value}`}>{t(`languages.${value}`)}</span>,
          })),
          onClick: ({ key }) => {
            setLocale(key);
            languageButton.current?.focus();
          },
        }}
      >
        <Button
          ref={languageButton}
          id="language-toggle"
          type="text"
          aria-label={t('ui.language')}
          aria-haspopup="menu"
          aria-expanded={languageOpen}
          title={t('ui.language')}
        >
          <Languages size={18} strokeWidth={1.8} aria-hidden />
          <span className="vn-language-label">{t(`languages.${language}`)}</span>
          <ChevronDown className="vn-language-chevron" size={14} strokeWidth={1.8} aria-hidden />
        </Button>
      </Dropdown>
      <Button
        id="theme-toggle"
        type="text"
        aria-label={t('theme.darkMode')}
        aria-pressed={isDark}
        title={t(isDark ? 'theme.switchToLight' : 'theme.switchToDark')}
        icon={<span className="vn-theme-indicator" aria-hidden="true" />}
        onClick={() => setTheme(isDark ? 'light' : 'dark')}
      />
    </div>
  );
}
