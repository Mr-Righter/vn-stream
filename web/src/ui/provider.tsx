import { App, ConfigProvider, theme as antdTheme } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import enUS from 'antd/locale/en_US';
import { useSyncExternalStore, useRef, type CSSProperties, type ReactNode } from 'react';
import { locale, theme } from './preferences-store.js';
import visualTheme from '../../../src/vn_stream/ui/theme.json';

function subscribe(callback: () => void) {
  document.addEventListener('languagechange', callback);
  document.addEventListener('themechange', callback);
  return () => {
    document.removeEventListener('languagechange', callback);
    document.removeEventListener('themechange', callback);
  };
}
export function usePreferences() {
  useSyncExternalStore(subscribe, () => `${locale()}:${theme()}`);
  return { language: locale(), appearance: theme() };
}
const motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
function subscribeMotion(callback: () => void) {
  motionQuery.addEventListener('change', callback);
  return () => motionQuery.removeEventListener('change', callback);
}
function useReducedMotion() {
  return useSyncExternalStore(subscribeMotion, () => motionQuery.matches);
}
function Surface({
  children,
  player,
  reduced,
}: {
  children: ReactNode;
  player: boolean;
  reduced: boolean;
}) {
  const { token } = antdTheme.useToken();
  const values = token as unknown as Record<string, string | number>;
  const variables = Object.fromEntries(
    Object.entries(visualTheme.cssTokens).map(([name, key]) => {
      const value = values[key];
      return [
        `--vn-${name}`,
        name.startsWith('motion-')
          ? reduced
            ? '0ms'
            : `${Math.round(parseFloat(String(value)) * 1000)}ms`
          : typeof value === 'number'
            ? `${value}${key.startsWith('lineHeight') || key.startsWith('fontWeight') ? '' : 'px'}`
            : value,
      ];
    }),
  );
  return (
    <App
      message={{ classNames: { root: 'vn-message' } }}
      className={`vn-app${player ? ' vn-player-app' : ''}`}
      style={
        {
          ...variables,
          '--vn-font-compact': `${visualTheme.compactFontSize}px`,
          '--vn-weight-body': visualTheme.fontWeightRegular,
          '--vn-weight-medium': visualTheme.fontWeightMedium,
        } as CSSProperties
      }
    >
      {children}
    </App>
  );
}
export function UIProvider({
  children,
  player = false,
  container,
}: {
  children: ReactNode;
  player?: boolean;
  container?: () => HTMLElement;
}) {
  const { language, appearance } = usePreferences();
  const reduced = useReducedMotion();
  // Ant 6 inserts its motion context lazily. Establish the opposite parent value
  // on mount so both wrappers keep a stable tree when the system setting changes.
  const motionBoundary = useRef(reduced);
  const colors = visualTheme.modes[appearance === 'dark' ? 'dark' : 'light'];
  return (
    <ConfigProvider theme={{ token: { motion: motionBoundary.current } }}>
      <ConfigProvider
        virtual={false}
        button={{ autoInsertSpace: false }}
        locale={language === 'en' ? enUS : zhCN}
        getPopupContainer={(trigger) =>
          container?.() ?? trigger?.closest<HTMLElement>('.vn-app') ?? document.body
        }
        theme={{
          algorithm: (seed, map) => ({
            ...(appearance === 'dark' ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm)(
              seed,
              map,
            ),
            ...visualTheme.token,
            ...colors,
          }),
          token: { ...visualTheme.token, ...colors, motion: !reduced },
          components: {
            Switch: {
              trackHeight: 32,
              trackMinWidth: 56,
              handleSize: 24,
              trackPadding: 4,
              handleBg: colors.colorBgElevated,
            },
            Button: {
              fontWeight: visualTheme.token.fontWeightStrong,
              primaryShadow: 'none',
              dangerColor:
                appearance === 'dark'
                  ? colors.colorBgLayout
                  : visualTheme.token.colorTextLightSolid,
              defaultHoverColor: colors.colorPrimaryText,
              defaultActiveColor: colors.colorPrimaryTextActive,
              defaultHoverBorderColor: colors.colorPrimaryBorder,
              defaultActiveBorderColor: colors.colorPrimaryBorder,
            },
            Segmented: {
              itemSelectedBg: colors.colorBgElevated,
              itemSelectedColor: colors.colorText,
              trackBg: appearance === 'dark' ? colors.colorBgContainer : colors.colorFillSecondary,
            },
          },
        }}
      >
        <Surface player={player} reduced={reduced}>
          {children}
        </Surface>
      </ConfigProvider>
    </ConfigProvider>
  );
}
