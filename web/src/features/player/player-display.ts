import type { DisplayState } from '../../api/player.js';
import { messageOf, type Message } from '../../ui/preferences-store.js';

function reconnectURL(session: string, revision: number | undefined) {
  const signature = `${session}:${revision}`;
  const url = new URL(location.href);
  // The URL bounds reloads even if storage is unavailable.
  if (url.searchParams.get('display_revision') === signature) return;
  const key = `vn-stream:display-reconnect-${session}-${revision}`;
  try {
    if (sessionStorage.getItem(key)) return;
    sessionStorage.setItem(key, '1');
  } catch {
    /* Storage is optional. */
  }
  url.searchParams.set('display_revision', signature);
  return url.href;
}

export class PlayerDisplay {
  private revision?: number;
  private mismatched?: string;
  private noticeShown = false;

  update(session: string, display: DisplayState) {
    const notice = this.displayNotice(session, display);
    const reconnect = this.checkGeometry(session, display);
    return { notice, reconnect };
  }

  private displayNotice(session: string, display: DisplayState): Message | undefined {
    if (!display.notice || this.noticeShown) return;
    this.noticeShown = true;
    const key = `vn-stream:display-notice-${session}`;
    try {
      if (sessionStorage.getItem(key) === '1') return;
      sessionStorage.setItem(key, '1');
    } catch {
      /* Storage is optional. */
    }
    const fallback =
      display.diagnostic === 'ack_write_failed'
        ? 'errors.displayHandshakeCouldNotBeCompletedTheGameContinues'
        : display.state === 'fallback' &&
            display.capture?.[0] === 1920 &&
            display.capture[1] === 1080
          ? 'errors.nativeCanvasUnavailableTemporarilyUsing19201080ItMay'
          : 'common.requestFailed';
    return messageOf(display.messages?.notice ?? display.notice, fallback);
  }

  private checkGeometry(session: string, display: DisplayState) {
    const { revision, capture } = display;
    const changed = this.revision === undefined ? revision > 1 : revision !== this.revision;
    this.revision = revision;
    // Selkies reads RandR geometry during its initial SETTINGS handshake. The
    // input sink can retain that geometry even when the video has resized.
    if (changed) return reconnectURL(session, revision);
    if (!capture) {
      this.mismatched = undefined;
      return;
    }
    const frames = [
      ...document.querySelectorAll<HTMLVideoElement | HTMLCanvasElement>(
        '#videoStream, #videoCanvas, #videoWorkerCanvas',
      ),
    ];
    const media = frames.find(
      (node) =>
        getComputedStyle(node).display !== 'none' &&
        (node instanceof HTMLVideoElement ? node.videoWidth > 0 : node.width > 0),
    );
    const size =
      media instanceof HTMLVideoElement
        ? [media.videoWidth, media.videoHeight]
        : media
          ? [media.width, media.height]
          : null;
    const input = document.querySelector<HTMLCanvasElement>('#videoCanvas');
    const inputMismatch =
      input &&
      input.width > 0 &&
      input.height > 0 &&
      (input.width !== capture[0] || input.height !== capture[1]);
    const mismatch = size && (size.some((n, i) => n !== capture[i]) || inputMismatch);
    const signature = `${session}:${revision}:${capture}`;
    const repeated = mismatch && this.mismatched === signature;
    // Allow initial codec negotiation two polls to settle.
    this.mismatched = mismatch ? signature : undefined;
    if (repeated) return reconnectURL(session, revision);
  }
}
