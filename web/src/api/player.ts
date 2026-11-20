import { AppError } from '../ui/preferences-store.js';
import { csrfToken } from './auth.js';
import { apiURL, requestData } from './client.js';
import type { DisplayState, Session } from './generated/types.gen.js';

export type { DisplayState, Session } from './generated/types.gen.js';

function validDisplay(value: unknown): value is DisplayState {
  if (!record(value)) return false;
  const { state, revision, capture, diagnostic, notice, messages } = value;
  return (
    typeof state === 'string' &&
    Number.isSafeInteger(revision) &&
    (capture == null ||
      (Array.isArray(capture) &&
        capture.length === 2 &&
        capture.every((size) => Number.isSafeInteger(size) && size > 0))) &&
    (diagnostic == null || typeof diagnostic === 'string') &&
    (notice == null || typeof notice === 'string') &&
    (messages == null || record(messages))
  );
}

export async function getSession(signal?: AbortSignal): Promise<Session> {
  const response = await requestData<unknown>('session', { signal, cache: 'no-store' });
  if (!record(response) || typeof response.state !== 'string')
    throw new AppError('common.requestFailed');
  const { project_id, session_id, display, exit_code } = response;
  return {
    ...(response as Session),
    project_id: typeof project_id === 'string' ? project_id : null,
    session_id: typeof session_id === 'string' && session_id ? session_id : null,
    exit_code: typeof exit_code === 'number' && Number.isSafeInteger(exit_code) ? exit_code : null,
    display: validDisplay(display) ? display : null,
  };
}

export const stopSession = () => requestData<Session>('session/stop', { method: 'POST' });

export type PlayerAction = 'rollback' | 'skip' | 'save' | 'load' | 'menu' | 'quit' | 'stop';

export type ControlStatus = {
  type: 'status';
  controller: boolean;
  epoch: string | null;
  ready: boolean;
  skip: boolean;
  available: readonly string[];
  supported: readonly string[];
  quit_pending?: boolean;
  last_quit_request?: string | null;
};

type ControlResult = {
  type: 'action';
  state: 'accepted' | 'dispatched' | 'rejected';
  request_id: string;
  epoch?: string;
};

type ControlMessage = ControlStatus | ControlResult | { type: 'superseded' } | { type: 'ended' };

type ControlCommand =
  | { type: 'hello'; csrf_token: string; session_id: string; connection_id: string }
  | { type: 'heartbeat'; epoch: string }
  | { type: 'action'; action: PlayerAction; epoch: string; request_id: string };

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function strings(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string');
}

function requestId(value: unknown): value is string {
  return typeof value === 'string' && /^[a-f0-9]{32}$/.test(value);
}

export function decodePlayerControl(data: unknown): ControlMessage | undefined {
  if (typeof data !== 'string') return;
  let message: unknown;
  try {
    message = JSON.parse(data);
  } catch {
    return;
  }
  if (!record(message)) return;
  switch (message.type) {
    case 'status': {
      const { type, controller, epoch, ready, skip, available, supported } = message;
      const { quit_pending, last_quit_request } = message;
      if (
        typeof controller === 'boolean' &&
        (epoch === null || (typeof epoch === 'string' && epoch.length > 0)) &&
        (!controller || epoch !== null) &&
        typeof ready === 'boolean' &&
        typeof skip === 'boolean' &&
        strings(supported) &&
        strings(available) &&
        available.every((action) => supported.includes(action)) &&
        (quit_pending === undefined || typeof quit_pending === 'boolean') &&
        (last_quit_request == null || requestId(last_quit_request))
      )
        return {
          type,
          controller,
          epoch,
          ready,
          skip,
          available,
          supported,
          quit_pending,
          last_quit_request,
        };
      return;
    }
    case 'action': {
      const { type, state, request_id, epoch } = message;
      if (
        (state === 'accepted' || state === 'dispatched' || state === 'rejected') &&
        requestId(request_id) &&
        (epoch === undefined || (typeof epoch === 'string' && epoch.length > 0))
      )
        return { type, state, request_id, epoch };
      return;
    }
    case 'superseded':
    case 'ended':
      return { type: message.type };
  }
}

export function sendPlayerControl(socket: WebSocket | undefined, command: ControlCommand) {
  if (socket?.readyState !== WebSocket.OPEN) return false;
  socket.send(JSON.stringify(command));
  return true;
}

export async function openPlayerControl(
  sessionId: string,
  connectionId: string,
  signal: AbortSignal,
) {
  const csrf = await csrfToken(signal);
  signal.throwIfAborted();
  const url = apiURL('session/control');
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  const socket = new WebSocket(url);
  socket.addEventListener(
    'open',
    () => {
      if (!signal.aborted)
        sendPlayerControl(socket, {
          type: 'hello',
          csrf_token: csrf,
          session_id: sessionId,
          connection_id: connectionId,
        });
    },
    { once: true },
  );
  return socket;
}
