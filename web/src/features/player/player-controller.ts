import { requireLogin } from '../../api/auth.js';
import { ApiError } from '../../api/client.js';
import {
  decodePlayerControl,
  getSession,
  openPlayerControl,
  sendPlayerControl,
  type ControlStatus,
  type PlayerAction,
} from '../../api/player.js';
import { message, messageOf, type MessageInput } from '../../ui/preferences-store.js';
import { Store } from '../../ui/store.js';
import { PlayerDisplay } from './player-display.js';

declare global {
  interface Window {
    __vnConnection?: string;
  }
}

type ControlView = Pick<ControlStatus, 'controller' | 'epoch' | 'ready' | 'skip' | 'available'> & {
  supported: ControlStatus['supported'] | null;
  exitPending: boolean;
};

type PlayerView = {
  control: ControlView;
  connection: MessageInput | null;
  notice: MessageInput | null;
  displayNotice: MessageInput | null;
};

type ExitAction = 'quit' | 'stop';

function sameActions(a: readonly string[] | null, b: readonly string[] | null) {
  return (
    a === b || (a !== null && b !== null && a.length === b.length && a.every((v, i) => v === b[i]))
  );
}

export class PlayerController extends Store<PlayerView> {
  private expectedSession = new URL(location.href).searchParams.get('session');
  private readonly connectionId = (window.__vnConnection ||= crypto.randomUUID());
  private readonly display = new PlayerDisplay();
  private active = false;
  private pageHidden = false;
  private destination?: string;
  private superseded = false;
  private poll?: AbortController;
  private pollTimer?: number;
  private opening?: AbortController;
  private socket?: WebSocket;
  private heartbeatTimer?: number;
  private readonly pendingActions = new Set<string>();
  private pendingExit?: { action: ExitAction; requestId: string };
  private gameQuitPending = false;
  private noticeTimer?: number;
  private displayNoticeTimer?: number;

  constructor() {
    super({
      control: {
        controller: false,
        epoch: null,
        ready: false,
        skip: false,
        supported: null,
        available: [],
        exitPending: false,
      },
      connection: null,
      notice: null,
      displayNotice: null,
    });
  }

  start = () => {
    this.active = true;
    this.pageHidden = false;
    document.addEventListener('visibilitychange', this.visibilityChanged);
    window.addEventListener('pagehide', this.pageHide);
    window.addEventListener('pageshow', this.pageShow);
    void this.check();
    return () => {
      this.active = false;
      document.removeEventListener('visibilitychange', this.visibilityChanged);
      window.removeEventListener('pagehide', this.pageHide);
      window.removeEventListener('pageshow', this.pageShow);
      this.stopActivity();
    };
  };

  notify = (notice: MessageInput) => {
    clearTimeout(this.noticeTimer);
    this.update({ notice });
    this.noticeTimer = window.setTimeout(() => this.update({ notice: null }), 4000);
  };

  nativeAction(action: PlayerAction) {
    const { controller, ready, epoch, supported, available, exitPending } = this.data.control;
    if (
      !this.canPoll() ||
      !controller ||
      !ready ||
      !epoch ||
      exitPending ||
      !supported?.includes(action) ||
      !available.includes(action)
    )
      return false;
    const request_id = crypto.randomUUID().replaceAll('-', '');
    if (!sendPlayerControl(this.socket, { type: 'action', action, epoch, request_id }))
      return false;
    this.pendingActions.add(request_id);
    if (action === 'quit' || action === 'stop') {
      this.pendingExit = { action, requestId: request_id };
      this.setControl({ ...this.data.control, exitPending: true });
    }
    return true;
  }

  private canPoll() {
    return this.active && !this.pageHidden && !this.destination && !document.hidden;
  }

  private visibilityChanged = () => {
    if (document.hidden) {
      this.cancelPoll();
      this.cancelOpening();
      this.stopHeartbeat();
      if (this.socket?.readyState === WebSocket.CONNECTING) this.closeControl();
    } else {
      this.heartbeat();
      void this.check();
    }
  };

  private pageHide = () => {
    this.pageHidden = true;
    this.stopActivity();
  };

  private pageShow = () => {
    if (!this.pageHidden) return;
    this.pageHidden = false;
    if (this.destination) location.replace(this.destination);
    else void this.check();
  };

  private stopActivity() {
    this.cancelPoll();
    this.closeControl();
    clearTimeout(this.noticeTimer);
    clearTimeout(this.displayNoticeTimer);
    if (this.data.notice || this.data.displayNotice)
      this.update({ notice: null, displayNotice: null });
  }

  private cancelPoll() {
    clearTimeout(this.pollTimer);
    const poll = this.poll;
    this.poll = undefined;
    poll?.abort();
  }

  private setConnection(connection: MessageInput | null) {
    if (connection !== this.data.connection) this.update({ connection });
  }

  private async check() {
    if (!this.canPoll() || this.poll) return;
    clearTimeout(this.pollTimer);
    const poll = new AbortController();
    this.poll = poll;
    const timeout = window.setTimeout(() => poll.abort(), 5000);
    poll.signal.addEventListener('abort', () => clearTimeout(timeout), { once: true });
    try {
      const session = await getSession(poll.signal);
      if (this.poll !== poll || !this.canPoll()) return;
      if (session.state === 'running') {
        if (!session.session_id) throw new Error('Missing running session ID');
        if (this.expectedSession && this.expectedSession !== session.session_id) {
          await this.returnToLibrary('ui.thePreviousSessionEndedAnotherSessionIsRunning');
          return;
        }
        this.expectedSession = session.session_id;
        this.setConnection(null);
        if (session.display) {
          const { reconnect, notice } = this.display.update(session.session_id, session.display);
          if (reconnect) {
            this.destination = reconnect;
            this.stopActivity();
            window.dispatchEvent(new Event('blur'));
            location.replace(reconnect);
            return;
          }
          if (notice) {
            this.update({ displayNotice: notice });
            this.displayNoticeTimer = window.setTimeout(
              () => this.update({ displayNotice: null }),
              8000,
            );
          }
        }
        void this.connect(session.session_id);
      } else if (session.state === 'stopped' || session.state === 'failed') {
        const exitMessage =
          session.state === 'stopped'
            ? 'ui.gameExited'
            : session.exit_code == null
              ? 'player.abnormalExit'
              : message('player.abnormalExitCode', { code: session.exit_code });
        await this.returnToLibrary(exitMessage);
      } else {
        this.closeControl();
        this.setConnection('ui.checkingThePreviousSessionPleaseWait');
      }
    } catch (error) {
      if (this.poll !== poll || !this.canPoll()) return;
      if (error instanceof ApiError && error.status === 401) {
        this.active = false;
        this.stopActivity();
      } else this.setConnection('ui.connectionInterruptedRetryingGameExitHasNotBeenConfirmed');
    } finally {
      clearTimeout(timeout);
      if (this.poll === poll) {
        this.poll = undefined;
        if (this.canPoll()) this.pollTimer = window.setTimeout(() => void this.check(), 1000);
      }
    }
  }

  private async returnToLibrary(input: MessageInput) {
    this.destination = '/library/';
    this.stopActivity();
    try {
      sessionStorage.setItem('vn-stream:return', JSON.stringify(messageOf(input)));
    } catch {
      /* Storage is optional. */
    }
    window.dispatchEvent(new Event('blur'));
    if (document.fullscreenElement) await document.exitFullscreen().catch(() => {});
    if (this.active && !this.pageHidden) location.replace(this.destination);
  }

  private async connect(sessionId: string) {
    if (this.superseded || this.opening || this.socket || !this.canPoll()) return;
    const opening = new AbortController();
    this.opening = opening;
    const timeout = window.setTimeout(() => opening.abort(), 5000);
    opening.signal.addEventListener('abort', () => clearTimeout(timeout), { once: true });
    try {
      const socket = await openPlayerControl(sessionId, this.connectionId, opening.signal);
      if (this.opening !== opening || !this.canPoll()) {
        socket.close();
        return;
      }
      this.socket = socket;
      socket.onmessage = (event) => {
        if (this.socket === socket) this.handleControl(event.data);
      };
      socket.onclose = (event) => {
        if (this.socket !== socket) return;
        this.socket = undefined;
        this.clearAuthority();
        if (event.code === 4401) {
          this.active = false;
          this.stopActivity();
          requireLogin();
        } else if (event.code === 4009) this.takeover();
      };
    } catch {
      /* The next session check retries transient control failures. */
    } finally {
      clearTimeout(timeout);
      if (this.opening === opening) this.opening = undefined;
    }
  }

  private cancelOpening() {
    const opening = this.opening;
    this.opening = undefined;
    opening?.abort();
  }

  private closeControl() {
    this.cancelOpening();
    const socket = this.socket;
    this.socket = undefined;
    if (socket) {
      socket.onmessage = null;
      socket.onclose = null;
      socket.close();
    }
    this.clearAuthority();
  }

  private clearAuthority() {
    this.stopHeartbeat();
    this.pendingActions.clear();
    this.pendingExit = undefined;
    this.gameQuitPending = false;
    this.setControl({ ...this.data.control, controller: false, epoch: null, exitPending: false });
  }

  private takeover() {
    this.superseded = true;
    this.closeControl();
    this.notify('ui.anotherPageHasTakenOverThisGame');
  }

  private handleControl(data: unknown) {
    const incoming = decodePlayerControl(data);
    if (!incoming) return;
    switch (incoming.type) {
      case 'status': {
        const previous = this.data.control;
        const sameAuthority =
          incoming.controller && previous.controller && incoming.epoch === previous.epoch;
        this.gameQuitPending = incoming.quit_pending === true;
        if (!sameAuthority) {
          this.stopHeartbeat();
          this.pendingActions.clear();
          this.pendingExit = undefined;
        }
        if (
          this.pendingExit?.action === 'quit' &&
          this.pendingExit.requestId === incoming.last_quit_request &&
          !incoming.quit_pending
        )
          this.pendingExit = undefined;
        this.setControl({
          controller: incoming.controller,
          epoch: incoming.epoch,
          ready: incoming.ready,
          skip: incoming.skip,
          available: incoming.available,
          supported: incoming.supported,
          exitPending: !!this.pendingExit || this.gameQuitPending,
        });
        if (this.heartbeatTimer === undefined) this.heartbeat();
        break;
      }
      case 'superseded':
        this.takeover();
        break;
      case 'ended':
        this.closeControl();
        this.cancelPoll();
        // Only an authoritative HTTP session snapshot returns to the library.
        void this.check();
        break;
      case 'action':
        if (
          incoming.state === 'accepted' ||
          !this.data.control.controller ||
          (incoming.epoch && incoming.epoch !== this.data.control.epoch) ||
          !this.pendingActions.delete(incoming.request_id) ||
          incoming.state === 'dispatched'
        )
          return;
        if (incoming.request_id === this.pendingExit?.requestId) {
          this.pendingExit = undefined;
          this.setControl({ ...this.data.control, exitPending: this.gameQuitPending });
        }
        this.notify('ui.thisActionIsCurrentlyUnavailableInTheGame');
        break;
    }
  }

  private setControl(control: ControlView) {
    const previous = this.data.control;
    if (
      previous.controller !== control.controller ||
      previous.epoch !== control.epoch ||
      previous.ready !== control.ready ||
      previous.skip !== control.skip ||
      previous.exitPending !== control.exitPending ||
      !sameActions(previous.supported, control.supported) ||
      !sameActions(previous.available, control.available)
    )
      this.update({ control });
  }

  private stopHeartbeat() {
    clearTimeout(this.heartbeatTimer);
    this.heartbeatTimer = undefined;
  }

  private heartbeat = () => {
    this.stopHeartbeat();
    const { controller, epoch } = this.data.control;
    if (
      this.canPoll() &&
      controller &&
      epoch &&
      sendPlayerControl(this.socket, { type: 'heartbeat', epoch })
    )
      this.heartbeatTimer = window.setTimeout(this.heartbeat, 5000);
  };
}
