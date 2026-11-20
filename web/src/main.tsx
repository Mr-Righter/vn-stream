import { createRoot } from 'react-dom/client';
import { LibraryApp } from './app/library-app.js';
import { ImportController } from './features/imports/import-controller.js';
import { LibraryController } from './features/library/library-controller.js';
import { UIProvider } from './ui/provider.js';

const library = new LibraryController();
const imports = new ImportController(library.added);

createRoot(document.getElementById('root')!).render(
  <UIProvider>
    <LibraryApp library={library} imports={imports} />
  </UIProvider>,
);

function start() {
  library.start();
  imports.start();
}

window.addEventListener('pageshow', start);
window.addEventListener('pagehide', () => {
  library.stopPolling();
  imports.stop();
});
start();
