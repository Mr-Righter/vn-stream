// Resize the stream container, then let Selkies fit both video and input together.
// iOS browser chrome changes visualViewport without always resizing the window.
let previous = '';
let scheduled = false;
const watched = new WeakSet<Element>();
const mediaChanges = new MutationObserver(schedule);
function fitServerMedia(width: number, height: number) {
  const client = window as Window & {
    enable_resize?: boolean;
    webrtcInput?: { resize?: () => void };
  };
  if (client.enable_resize !== false) return;
  // The pinned client's enable_resize=false handler skips local fitting too.
  // Keep both rendered pixels and its existing input adapter on one rectangle.
  const media = [
    ...document.querySelectorAll<HTMLVideoElement | HTMLCanvasElement>(
      '#videoStream, #videoCanvas, #videoWorkerCanvas',
    ),
  ];
  const source = media.find(
    (node) =>
      getComputedStyle(node).display !== 'none' &&
      ((node instanceof HTMLVideoElement && node.videoWidth > 0) ||
        (node instanceof HTMLCanvasElement && node.width > 0)),
  );
  if (!source) return;
  const sw = source instanceof HTMLVideoElement ? source.videoWidth : source.width;
  const sh = source instanceof HTMLVideoElement ? source.videoHeight : source.height;
  if (!sw || !sh) return;
  const ratio = Math.min(width / sw, height / sh);
  const w = sw * ratio,
    h = sh * ratio;
  const style = {
    position: 'absolute',
    width: `${w}px`,
    height: `${h}px`,
    left: `${(width - w) / 2}px`,
    top: `${(height - h) / 2}px`,
  };
  let changed = false;
  for (const node of [...media, document.querySelector<HTMLElement>('#overlayInput')]) {
    if (!node) continue;
    if (!watched.has(node)) {
      watched.add(node);
      mediaChanges.observe(node, {
        attributes: true,
        attributeFilter: ['width', 'height', 'style'],
      });
    }
    for (const [name, value] of Object.entries(style)) {
      if (
        node.style.getPropertyValue(name) !== value ||
        node.style.getPropertyPriority(name) !== 'important'
      ) {
        node.style.setProperty(name, value, 'important');
        changed = true;
      }
    }
  }
  if (changed) client.webrtcInput?.resize?.();
}
function fit() {
  scheduled = false;
  const viewport = window.visualViewport;
  const values = [
    viewport?.width ?? window.innerWidth,
    viewport?.height ?? window.innerHeight,
    viewport?.offsetLeft ?? 0,
    viewport?.offsetTop ?? 0,
  ];
  const signature = values.join(',');
  if (signature !== previous) {
    previous = signature;
    ['width', 'height', 'left', 'top'].forEach((name, index) => {
      document.documentElement.style.setProperty(`--player-${name}`, `${values[index]}px`);
    });
    window.dispatchEvent(new Event('resize'));
  }
  fitServerMedia(values[0], values[1]);
}
function schedule() {
  if (scheduled) return;
  scheduled = true;
  requestAnimationFrame(fit);
}
window.addEventListener('resize', schedule, true);
document.addEventListener('loadedmetadata', schedule, true);
window.addEventListener('message', schedule);
new MutationObserver(schedule).observe(document.body, { childList: true, subtree: true });
window.visualViewport?.addEventListener('resize', schedule);
window.visualViewport?.addEventListener('scroll', schedule);
document.addEventListener('fullscreenchange', schedule);
schedule();
export {};
