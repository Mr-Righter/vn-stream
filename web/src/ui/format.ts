import { locale, t } from './preferences-store.js';
export const bytes = (value: number) =>
  value < 1024
    ? `${value} B`
    : value < 1024 ** 2
      ? `${(value / 1024).toFixed(1)} KB`
      : value < 1024 ** 3
        ? `${(value / 1024 ** 2).toFixed(1)} MB`
        : `${(value / 1024 ** 3).toFixed(2)} GB`;
export const date = (value: string | null) =>
  value
    ? new Date(value).toLocaleString(locale() === 'en' ? 'en-US' : 'zh-CN')
    : t('ui.notPlayedYet');
