import visualTheme from '../../../src/vn_stream/ui/theme.json';
import zh from '../../../src/vn_stream/i18n/locales/zh-CN.json';
import en from '../../../src/vn_stream/i18n/locales/en.json';
import legacy from '../../../src/vn_stream/i18n/locales/legacy.json';

export type Message = { code: string; params?: Record<string, string | number> };
export type MessageInput = Message | string;
const catalogs: Record<string, Record<string, string>> = { zh, en };
const aliases: Record<string, string> = { ...legacy };
for (const [code, value] of Object.entries(en)) aliases[value] ??= code;
export const isKey = (value: string) => Object.hasOwn(zh, value);
function saved(key: string) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}
function save(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* Preferences remain active in memory. */
  }
}
function readPreferences(value: string | null = saved('vn-stream:preferences')): {
  language?: string;
  appearance?: string;
} {
  try {
    return JSON.parse(value ?? '{}');
  } catch {
    return {};
  }
}
let language = readPreferences().language === 'en' ? 'en' : 'zh';
let appearance = readPreferences().appearance === 'light' ? 'light' : 'dark';
function persistPreferences() {
  save('vn-stream:preferences', JSON.stringify({ language, appearance }));
}
export const locale = () => language;
export const languageHeader = () => (language === 'en' ? 'en' : 'zh-CN');
export const theme = () => appearance;
export const message = (code: string, params: Record<string, string | number> = {}): Message => ({
  code,
  params,
});
export function messageOf(value: unknown, fallback = 'common.requestFailed'): Message {
  if (value instanceof AppError) return value.descriptor;
  if (value instanceof Error) return messageOf(value.message, fallback);
  if (value && typeof value === 'object' && 'code' in value && typeof value.code === 'string') {
    const params = 'params' in value ? value.params : {};
    if (
      params &&
      typeof params === 'object' &&
      !Array.isArray(params) &&
      Object.values(params).every(
        (v) => typeof v === 'string' || (typeof v === 'number' && Number.isFinite(v)),
      )
    )
      return { code: value.code, params: params as Message['params'] };
  }
  if (typeof value === 'string') {
    if (isKey(value)) return message(value);
    if (Object.hasOwn(aliases, value)) return message(aliases[value]);
  }
  return message(fallback);
}
export function t(input: MessageInput, params?: Record<string, string | number>): string {
  const item = messageOf(input);
  if (params) item.params = params;
  const template = catalogs[language][item.code];
  if (!Object.hasOwn(catalogs[language], item.code) || typeof template !== 'string')
    return catalogs[language]['common.requestFailed'];
  const names = [...template.matchAll(/\{([A-Za-z0-9_]+)\}/g)].map((m) => m[1]);
  if (names.some((name) => !Object.hasOwn(item.params ?? {}, name)))
    return catalogs[language]['common.requestFailed'];
  return template.replace(/\{([A-Za-z0-9_]+)\}/g, (_, name) => String(item.params![name]));
}
export class AppError extends Error {
  descriptor: Message;
  constructor(value: MessageInput) {
    const descriptor = messageOf(value);
    super(t(descriptor));
    this.descriptor = descriptor;
  }
}
export function responseError(payload: unknown, status: number): AppError {
  const body = payload && typeof payload === 'object' ? (payload as { error?: Message }) : {};
  return new AppError(messageOf(body.error, 'common.requestFailed'));
}
function applyTheme() {
  document.documentElement.dataset.theme = appearance;
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute(
      'content',
      visualTheme.modes[appearance === 'dark' ? 'dark' : 'light'].colorBgLayout,
    );
}
export function setLocale(value: string) {
  language = value === 'en' ? 'en' : 'zh';
  persistPreferences();
  document.documentElement.lang = languageHeader();
  document.dispatchEvent(new Event('languagechange'));
}
export function setTheme(value: string) {
  appearance = value === 'light' ? 'light' : 'dark';
  persistPreferences();
  applyTheme();
  document.dispatchEvent(new Event('themechange'));
}
window.addEventListener('storage', (event) => {
  if (event.key !== 'vn-stream:preferences') return;
  const preferences = readPreferences(event.newValue);
  language = preferences.language === 'en' ? 'en' : 'zh';
  appearance = preferences.appearance === 'light' ? 'light' : 'dark';
  document.documentElement.lang = languageHeader();
  applyTheme();
  document.dispatchEvent(new Event('languagechange'));
  document.dispatchEvent(new Event('themechange'));
});
document.documentElement.lang = languageHeader();
applyTheme();
