import { createRoot } from 'react-dom/client';
import { PlayerController } from './features/player/player-controller.js';
import { PlayerToolbar } from './features/player/player-toolbar.js';
import './features/player/player-viewport.js';
import { UIProvider } from './ui/provider.js';

const root = document.createElement('section');
root.id = 'vn-game-controls';
root.className = 'allow-native-input';
(document.fullscreenElement ?? document.body).append(root);
const player = new PlayerController();
createRoot(root).render(
  <UIProvider player container={() => root.querySelector<HTMLElement>('.vn-app') ?? root}>
    <PlayerToolbar player={player} host={root} />
  </UIProvider>,
);
const style = document.createElement('link');
style.rel = 'stylesheet';
const source = new URL(import.meta.url);
const href = new URL(
  source.pathname.includes('/chunks/') ? '../game-controls.css' : './game-controls.css',
  source,
);
href.search = new URL(import.meta.url).search;
style.href = href.href;
document.head.append(style);
