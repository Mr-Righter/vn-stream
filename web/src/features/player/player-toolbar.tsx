import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { Alert, Button, Dropdown, Switch, Modal } from 'antd';
import {
  ChevronDown,
  FastForward,
  FolderOpen,
  Keyboard,
  List,
  Power,
  RotateCcw,
  Save,
  Settings,
} from 'lucide-react';
import { t } from '../../ui/preferences-store.js';
import { usePreferences } from '../../ui/provider.js';
import type { PlayerController } from './player-controller.js';

export function PlayerToolbar({ player, host }: { player: PlayerController; host: HTMLElement }) {
  const { appearance, language } = usePreferences();
  const { control, connection, notice, displayNotice } = useSyncExternalStore(
    player.subscribe,
    player.getSnapshot,
  );
  useEffect(player.start, [player]);
  const keyboard = useRef<HTMLInputElement | null>(null);
  const [open, setOpen] = useState(false);
  const [stopOpen, setStopOpen] = useState(false);
  const exitAction = control.supported?.includes('stop')
    ? 'stop'
    : control.supported?.includes('quit')
      ? 'quit'
      : null;
  const [shortcuts, setShortcuts] = useState(() => {
    try {
      return localStorage.getItem('vn-stream:shortcuts') !== 'off';
    } catch {
      return true;
    }
  });
  useEffect(() => {
    host.dataset.theme = appearance;
    host.setAttribute('aria-label', t('ui.gameShortcuts'));
    keyboard.current?.setAttribute('aria-label', t('ui.gameTextInput'));
  }, [appearance, host, language]);
  useEffect(() => {
    function mount() {
      const parent = document.fullscreenElement ?? document.body;
      if (host.parentElement !== parent) parent.append(host);
      const input = keyboard.current;
      if (input?.classList.contains('game-keyboard-receiver') && input.parentElement !== parent)
        parent.append(input);
    }
    function blurKeyboard(event: PointerEvent) {
      if (
        event.target instanceof Element &&
        event.target.closest('#overlayInput, #videoStream, #videoCanvas, #videoWorkerCanvas')
      )
        keyboard.current?.blur();
    }
    mount();
    document.addEventListener('fullscreenchange', mount);
    document.addEventListener('pointerdown', blurKeyboard, true);
    return () => {
      document.removeEventListener('fullscreenchange', mount);
      document.removeEventListener('pointerdown', blurKeyboard, true);
    };
  }, [host]);

  function showKeyboard() {
    const input = document.getElementById('keyboard-input-assist') as HTMLInputElement | null;
    if (!input) {
      player.notify('ui.theStreamIsNotReadyPleaseTryAgain');
      return;
    }
    if (keyboard.current !== input)
      input.addEventListener('blur', () => input.setAttribute('aria-hidden', 'true'));
    keyboard.current = input;
    input.classList.add('game-keyboard-receiver');
    input.classList.remove('allow-native-input');
    input.removeAttribute('aria-hidden');
    input.setAttribute('aria-label', t('ui.gameTextInput'));
    input.removeAttribute('placeholder');
    input.tabIndex = -1;
    input.type = 'text';
    input.style.cssText = '';
    const parent = document.fullscreenElement ?? document.body;
    if (input.parentElement !== parent) parent.append(input);
    // iOS requires focus within the original synchronous click gesture.
    input.focus({ preventScroll: true });
  }
  function toggle(value: boolean) {
    setShortcuts(value);
    try {
      localStorage.setItem('vn-stream:shortcuts', value ? 'on' : 'off');
    } catch {
      /* Storage is optional. */
    }
  }
  const disabled = !control.controller || !control.ready || control.exitPending;
  const exitDisabled = disabled || !exitAction || !control.available.includes(exitAction);
  return (
    <>
      <Dropdown
        open={open}
        onOpenChange={setOpen}
        trigger={['click']}
        placement="bottomLeft"
        popupRender={() => (
          <div id="game-settings" className="vn-player-menu">
            <div className="shortcut-switch">
              <span>{t('ui.shortcuts')}</span>
              <Switch checked={shortcuts} aria-label={t('ui.shortcuts')} onChange={toggle} />
            </div>
            {exitAction && (
              <Button
                type="text"
                danger
                className="quit-game"
                icon={<Power size={18} />}
                aria-label={t(exitAction === 'stop' ? 'ui.stopGame' : 'ui.quitGame')}
                disabled={exitDisabled}
                onClick={() => {
                  if (exitAction === 'stop') {
                    setOpen(false);
                    setStopOpen(true);
                  } else if (player.nativeAction('quit')) setOpen(false);
                }}
              >
                {t(exitAction === 'stop' ? 'ui.stopGame' : 'ui.quit')}
              </Button>
            )}
          </div>
        )}
      >
        <Button
          id="game-settings-toggle"
          shape="circle"
          icon={<Settings size={20} />}
          aria-label={t('ui.gameSettings')}
        />
      </Dropdown>
      <Modal
        open={stopOpen}
        title={t('ui.stopGame')}
        onCancel={() => setStopOpen(false)}
        onOk={() => {
          if (player.nativeAction('stop')) setStopOpen(false);
        }}
        okButtonProps={{ danger: true, disabled: exitDisabled }}
      >
        {t('engine.stopConfirm')}
      </Modal>
      <nav
        id="game-shortcuts"
        aria-label={t('ui.shortcuts')}
        hidden={!shortcuts || !control.supported}
      >
        <Button type="text" aria-label={t('ui.keyboard')} onClick={showKeyboard}>
          <Keyboard size={16} />
          <span>{t('ui.keyboard')}</span>
        </Button>
        {(
          [
            ['rollback', 'ui.back', RotateCcw],
            ['skip', 'ui.skip', FastForward],
            ['save', 'ui.save', Save],
            ['load', 'ui.load', FolderOpen],
            ['menu', 'ui.menu', List],
          ] as const
        )
          .filter(([action]) => control.supported?.includes(action))
          .map(([action, label, Icon]) => (
            <Button
              key={action}
              type="text"
              aria-label={t(label)}
              aria-pressed={action === 'skip' ? control.skip : undefined}
              disabled={disabled || !control.available.includes(action)}
              onClick={() => {
                if (!player.nativeAction(action))
                  player.notify('ui.thisActionIsCurrentlyUnavailableInTheGame');
              }}
            >
              <Icon size={16} />
              <span>{t(label)}</span>
            </Button>
          ))}
        <Button
          type="text"
          aria-label={t('ui.hideShortcuts')}
          onClick={() => {
            toggle(false);
            keyboard.current?.blur();
          }}
        >
          <ChevronDown size={16} />
          <span>{t('ui.hide')}</span>
        </Button>
      </nav>
      {notice && (
        <Alert
          className="game-control-notice"
          role="status"
          title={t(notice)}
          type="info"
          showIcon
        />
      )}
      <div className="vn-player-statuses">
        {connection && (
          <Alert
            className="player-status"
            role="status"
            title={t(connection)}
            type="warning"
            showIcon
          />
        )}
        {displayNotice && (
          <Alert
            className="player-status"
            role="status"
            title={t(displayNotice)}
            type="info"
            showIcon
          />
        )}
      </div>
    </>
  );
}
