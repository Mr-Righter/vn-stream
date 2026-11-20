import {
  contentHash,
  navigateContent,
  parseContentRoute,
  useContentScroll,
  type ContentRoute,
} from './content-route.js';
import { confirmDraftNavigation } from './content-navigation.js';
import { getHashSnapshot, subscribeHash } from './hash-navigation.js';
import { useEffect, useState, useSyncExternalStore } from 'react';
import { Alert, Button, Dropdown, Modal, Space, Typography } from 'antd';
import { ArrowLeft, Archive, LogOut, Plus, RefreshCw, Settings } from 'lucide-react';
import { logout } from '../api/auth.js';
import type { Project, ProjectCapabilities } from '../api/types.js';
import { messageOf, t, type MessageInput } from '../ui/preferences-store.js';
import { usePreferences } from '../ui/provider.js';
import { AppHeader } from '../ui/app-header.js';
import { LibraryPage } from '../features/library/library-page.js';
import { GameDetails } from '../features/project-details/game-details.js';
import type { LibraryController } from '../features/library/library-controller.js';
import type { ImportController } from '../features/imports/import-controller.js';
import { AddPage } from '../features/imports/add-page.js';
import { AssetsPage } from '../features/assets/assets-page.js';
import { SavesPage } from '../features/saves/saves-page.js';

function visibleProjectRoute(route: ContentRoute, capabilities: ProjectCapabilities): ContentRoute {
  if (
    (route.tab === 'assets' && !capabilities.assets) ||
    (route.tab === 'saves' && !capabilities.save_management)
  )
    return { ...route, tab: 'info', view: 'home' };
  if (route.view === 'migrate' && !capabilities.save_migration) return { ...route, view: 'home' };
  return route;
}

export function LibraryApp({
  library,
  imports,
}: {
  library: LibraryController;
  imports: ImportController;
}) {
  const { language } = usePreferences();
  const state = useSyncExternalStore(library.subscribe, library.getSnapshot);
  useSyncExternalStore(imports.subscribe, imports.getSnapshot);
  const hash = useSyncExternalStore(subscribeHash, getHashSnapshot);
  const requestedRoute = parseContentRoute(hash);
  useContentScroll(hash);
  const game = state.games.find((item) => item.project_id === requestedRoute?.projectId);
  const route = requestedRoute && game && visibleProjectRoute(requestedRoute, game.capabilities);
  const independent = !!route && route.tab !== 'info' && route.view !== 'home';
  const adding = hash === '#add';
  const [menuOpen, setMenuOpen] = useState(false);
  const [dialog, setDialog] = useState<{ kind: 'delete' | 'stop'; game: Project } | null>(null);
  const [busy, setBusy] = useState(false);
  const [dialogError, setDialogError] = useState<MessageInput | null>(null);
  useEffect(() => {
    document.title = `${t('ui.myGames')} · VN Stream`;
  }, [language]);
  const confirm = (kind: 'delete' | 'stop', target: Project) => {
    setDialogError(null);
    setDialog({ kind, game: target });
  };
  async function confirmAction() {
    if (!dialog || busy) return;
    setBusy(true);
    setDialogError(null);
    try {
      if (dialog.kind === 'delete') await library.remove(dialog.game);
      else await library.stop();
      setDialog(null);
    } catch (error) {
      setDialogError(messageOf(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <AppHeader onHome={() => navigateContent('')}>
        <Dropdown
          trigger={['click']}
          placement="bottomRight"
          autoFocus
          onOpenChange={setMenuOpen}
          menu={{
            items: [
              {
                key: 'add',
                icon: <Plus size={16} />,
                label: <span id="add-game">{t('ui.addGame')}</span>,
              },
              ...(imports.active
                ? [
                    {
                      key: 'active',
                      icon: <Archive size={16} />,
                      label: <span id="active-import">{t('ui.viewImportTask')}</span>,
                    },
                  ]
                : []),
              {
                key: 'refresh',
                icon: <RefreshCw size={16} />,
                label: <span id="refresh">{t('ui.refreshLibrary')}</span>,
              },
              { type: 'divider' },
              {
                key: 'logout',
                icon: <LogOut size={16} />,
                label: <span id="logout">{t('ui.signOut')}</span>,
              },
            ],
            onClick: ({ key }) => {
              if (key === 'add' || key === 'active') navigateContent('#add');
              if (key === 'refresh') void library.action(library.rescan);
              if (key === 'logout')
                void confirmDraftNavigation(() => {
                  void library.action(logout);
                });
            },
          }}
        >
          <Button
            id="menu-toggle"
            type="text"
            icon={<Settings size={18} strokeWidth={1.8} aria-hidden />}
            aria-label={t('ui.manageLibrary')}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            title={t('ui.manageLibrary')}
          />
        </Dropdown>
      </AppHeader>
      <main className="vn-shell">
        {!independent && (
          <header className="vn-route-heading">
            {(game || adding) && (
              <Button
                id="back"
                type="text"
                icon={<ArrowLeft size={20} />}
                aria-label={t('ui.backToLibrary')}
                onClick={() => navigateContent('')}
              />
            )}
            <Typography.Title level={1}>
              {t(adding ? 'ui.addGame' : game ? 'ui.gameDetails' : 'ui.myGames')}
            </Typography.Title>
          </header>
        )}
        <div id="message" role="status" aria-live="polite">
          {state.notice && (
            <Alert
              title={
                t(state.notice) +
                (state.scanTruncated && messageOf(state.notice).code === 'library.refreshSummary'
                  ? t('library.scanLimit')
                  : '')
              }
              type="info"
              showIcon
            />
          )}
        </div>
        <div hidden={!!game || adding}>
          <LibraryPage library={library} />
        </div>
        {game && route && (
          <GameDetails
            key={game.project_id}
            game={game}
            tab={route.tab}
            independent={independent}
            session={state.session}
            starting={state.starting}
            runtimeChoice={library.choice(game)}
            actions={{
              chooseRuntime: (value) => library.choose(game, value),
              changeTab: (tab) => navigateContent(contentHash(game.project_id, tab)),
              play: () => {
                void confirmDraftNavigation(() => {
                  void library.action(() => library.play(game));
                });
              },
              stop: () => confirm('stop', game),
              remove: () => {
                if (game.deletion?.state === 'failed') {
                  void library.action(() => library.retryDeletion(game));
                } else {
                  confirm('delete', game);
                }
              },
            }}
          >
            {game.capabilities.assets && (
              <AssetsPage
                projectId={game.project_id}
                hidden={route.tab !== 'assets'}
                route={route}
              />
            )}
            {game.capabilities.save_management && (
              <SavesPage
                projectId={game.project_id}
                route={route}
                canEdit={game.capabilities.save_editing}
                canMigrate={game.capabilities.save_migration}
              />
            )}
          </GameDetails>
        )}
        <AddPage controller={imports} hidden={!adding} engines={state.engines} />
        <Modal
          getContainer={() => document.querySelector<HTMLElement>('.vn-app') ?? document.body}
          open={!!dialog}
          title={t(dialog?.kind === 'stop' ? 'ui.stopGame' : 'ui.deleteGame')}
          onCancel={() => !busy && setDialog(null)}
          footer={
            <Space wrap>
              <Button disabled={busy} onClick={() => setDialog(null)}>
                {t('ui.cancel')}
              </Button>
              <Button danger type="primary" loading={busy} onClick={() => void confirmAction()}>
                {t(dialog?.kind === 'stop' ? 'ui.stopGame' : 'ui.deletePermanently')}
              </Button>
            </Space>
          }
        >
          <div id="delete-dialog">
            <Typography.Text strong>{dialog?.game.name}</Typography.Text>
            <Typography.Paragraph>
              {t(
                dialog?.kind === 'stop'
                  ? 'ui.saveFirstUnsavedProgressWillBeLostWhenStopping'
                  : 'ui.permanentlyDeleteTheProjectAndItsSavesThisCannot',
              )}
            </Typography.Paragraph>
            {dialog?.kind === 'delete' && <code className="delete-path">{dialog.game.path}</code>}
            {dialogError && <Alert title={t(dialogError)} type="error" showIcon />}
          </div>
        </Modal>
      </main>
    </>
  );
}
