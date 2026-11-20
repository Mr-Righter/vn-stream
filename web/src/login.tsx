import { createRoot } from 'react-dom/client';
import { useEffect, useState } from 'react';
import { Alert, Button, Card, Checkbox, Form, Input, Typography } from 'antd';
import { Eye, EyeOff } from 'lucide-react';
import { apiURL, signIn } from './api/client.js';
import type { Login as Credentials } from './api/generated/types.gen.js';
import { messageOf, t, type Message } from './ui/preferences-store.js';
import { UIProvider, usePreferences } from './ui/provider.js';
import { AppHeader } from './ui/app-header.js';

function destination() {
  const fallback = new URL('./', location.href);
  const raw = new URL(location.href).searchParams.get('next');
  if (!raw || !raw.startsWith('/') || raw.startsWith('//')) return fallback.href;
  try {
    const candidate = new URL(raw, location.origin);
    let decoded = candidate.pathname;
    for (let i = 0; i < 3; i++) decoded = decodeURIComponent(decoded);
    if (
      candidate.origin !== location.origin ||
      decoded.startsWith('//') ||
      decoded.includes('\\') ||
      /[\x00-\x1f]/.test(decoded) ||
      /\/login(?:\.html)?$/.test(decoded)
    )
      return fallback.href;
    if (!candidate.hash) candidate.hash = location.hash;
    return candidate.href;
  } catch {
    return fallback.href;
  }
}
function Login() {
  usePreferences();
  const [form] = Form.useForm();
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<Message | null>(null);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    document.title = `${t('ui.signIn')} · VN Stream`;
  });
  async function submit(values: Credentials) {
    if (busy) return;
    for (const name of ['username', 'password'] as const) {
      if (!values[name]) {
        setFailure(
          messageOf(name === 'username' ? 'auth.usernameRequired' : 'auth.passwordRequired'),
        );
        document.getElementById(name)?.focus();
        return;
      }
    }
    setBusy(true);
    setFailure(null);
    try {
      await signIn(values);
      form.setFieldValue('password', '');
      location.replace(destination());
    } catch (error) {
      setFailure(messageOf(error, 'common.networkFailed'));
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="vn-shell vn-login-shell">
      <Card className="vn-login-card">
        <Typography.Title level={1}>{t('ui.welcomeBack')}</Typography.Title>
        <Typography.Paragraph type="secondary">
          {t('ui.signInToContinueYourStory')}
        </Typography.Paragraph>
        <Form
          id="login-form"
          form={form}
          layout="vertical"
          requiredMark={false}
          initialValues={{ remember: true }}
          onFinish={submit}
          method="post"
          action={apiURL('auth/login').pathname}
        >
          <Form.Item label={t('ui.username')} name="username">
            <Input
              id="username"
              name="username"
              size="large"
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
            />
          </Form.Item>
          <Form.Item label={t('ui.password')} name="password">
            <Input.Password
              id="password"
              name="password"
              size="large"
              autoComplete="current-password"
              visibilityToggle={{ visible, onVisibleChange: setVisible }}
              iconRender={(shown) => (
                <button
                  type="button"
                  className="vn-password-toggle"
                  aria-label={t(shown ? 'auth.hidePassword' : 'auth.showPassword')}
                >
                  {shown ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              )}
            />
          </Form.Item>
          <Form.Item name="remember" valuePropName="checked">
            <Checkbox id="remember" name="remember">
              {t('ui.keepMeSignedInFor30Days')}
            </Checkbox>
          </Form.Item>
          <div id="login-error" role="alert">
            {failure && <Alert title={t(failure)} type="error" showIcon />}
          </div>
          <Button
            id="login-submit"
            htmlType="submit"
            type="primary"
            size="large"
            block
            loading={busy}
          >
            {t('ui.signIn')}
          </Button>
        </Form>
      </Card>
    </main>
  );
}
createRoot(document.getElementById('root')!).render(
  <UIProvider>
    <AppHeader />
    <Login />
  </UIProvider>,
);
