import { Segmented } from '../../ui/segments.js';
import { useEffect, useState, useSyncExternalStore } from 'react';
import { Alert, Button, Empty, Input, Select, Skeleton, Typography } from 'antd';
import { Play, Search, X } from 'lucide-react';
import { contentHash, navigateContent } from '../../app/content-route.js';
import { t } from '../../ui/preferences-store.js';
import { date } from '../../ui/format.js';
import { GameAvatar } from '../../ui/game-avatar.js';
import type { LibraryController } from './library-controller.js';

type CatalogFilters = { query: string; engine: string; recent: boolean };
const storageKey = 'vn-stream:library-state';
function restoreFilters(): CatalogFilters {
  const defaults = { query: '', engine: 'all', recent: false };
  try {
    const value = JSON.parse(sessionStorage.getItem(storageKey) ?? 'null');
    if (!value || typeof value !== 'object') return defaults;
    return {
      query: typeof value.query === 'string' ? value.query : '',
      engine: typeof value.engine === 'string' ? value.engine : 'all',
      recent: value.recent === true,
    };
  } catch {
    return defaults;
  }
}

export function LibraryPage({ library }: { library: LibraryController }) {
  const state = useSyncExternalStore(library.subscribe, library.getSnapshot);
  const [filters, setFilters] = useState(restoreFilters);
  const { query, engine, recent } = filters;
  const updateFilters = (change: Partial<CatalogFilters>) =>
    setFilters((previous) => ({ ...previous, ...change }));
  useEffect(() => {
    try {
      sessionStorage.setItem(storageKey, JSON.stringify(filters));
    } catch {
      // Filtering remains usable when browser storage is unavailable.
    }
  }, [filters]);
  const search = query.trim().toLowerCase();
  const filtered = search !== '' || engine !== 'all';
  const groupKey = (name: string) => (/^[A-Za-z]/.test(name) ? name[0].toUpperCase() : 'other');
  const sorted = state.games
    .filter((game) => !recent || game.last_played_at !== null)
    .filter((game) => game.name.toLowerCase().includes(search))
    .filter((game) => engine === 'all' || game.engine_id === engine)
    .sort((a, b) =>
      recent
        ? Date.parse(b.last_played_at ?? '1970-01-01') -
          Date.parse(a.last_played_at ?? '1970-01-01')
        : (groupKey(a.name) === 'other' ? 1 : 0) - (groupKey(b.name) === 'other' ? 1 : 0) ||
          a.name.localeCompare(b.name, 'en', { sensitivity: 'base' }),
    );
  const groups = recent ? ['recent'] : [...new Set(sorted.map((game) => groupKey(game.name)))];
  const current = state.games.find((game) => game.project_id === state.session.project_id);
  return (
    <div id="catalog">
      <Segmented
        className="segments"
        block
        size="large"
        aria-label={t('ui.gameList')}
        value={recent ? 'recent' : 'all'}
        onChange={(value) => updateFilters({ recent: value === 'recent' })}
        options={[
          { value: 'recent', label: <span id="recent">{t('ui.recentlyPlayed')}</span> },
          { value: 'all', label: <span id="all">{t('ui.allGames')}</span> },
        ]}
      />
      <div className="vn-library-filters">
        <Input
          size="large"
          aria-label={t('library.searchName')}
          placeholder={t('library.searchName')}
          prefix={<Search size={16} aria-hidden="true" />}
          allowClear={{
            clearIcon: <X size={16} aria-hidden="true" />,
            label: t('library.clearSearch'),
          }}
          autoComplete="off"
          value={query}
          onChange={(event) => updateFilters({ query: event.target.value })}
        />
        <Select
          size="large"
          aria-label={t('library.filterEngine')}
          showSearch={false}
          autoComplete="off"
          value={engine}
          onChange={(value) => updateFilters({ engine: value })}
          options={[
            { value: 'all', label: t('library.allEngines') },
            ...state.engines.map((engine) => ({
              value: engine.engine_id,
              label: engine.display_name,
            })),
          ]}
        />
      </div>
      <div id="active">
        {state.session.state === 'running' && (
          <Button
            className="resume"
            icon={<Play size={16} />}
            disabled={!!state.starting || !current}
            onClick={() => current && void library.action(() => library.play(current))}
          >
            {t('library.resumeGame', { name: current?.name ?? t('ui.currentGame') })}
          </Button>
        )}
        {state.session.state === 'loading' && (
          <Typography.Text type="secondary">{t('ui.checkingGameStatus')}</Typography.Text>
        )}
        {state.session.state === 'recovering' && (
          <Alert title={t('ui.cleaningUpThePreviousSessionRestartTheServiceIf')} type="warning" />
        )}
      </div>
      <div id="games" aria-busy={!state.loaded && !state.failed}>
        {!state.loaded ? (
          state.failed ? (
            <>
              <Alert title={t('ui.unableToLoadYourLibraryPleaseRetry')} type="error" />
              <Button onClick={() => void library.action(() => library.refresh())}>
                {t('ui.retry')}
              </Button>
            </>
          ) : (
            <>
              <Typography.Text type="secondary">{t('ui.loadingYourLibrary')}</Typography.Text>
              <Skeleton active title paragraph={{ rows: 3 }} />
            </>
          )
        ) : (
          groups.map((group) => (
            <section
              className="game-group"
              key={group}
              aria-labelledby={recent ? undefined : `letter-${group}`}
            >
              {!recent && (
                <Typography.Title level={2} id={`letter-${group}`} className="letter">
                  {group === 'other' ? t('ui.other') : group}
                </Typography.Title>
              )}
              <div className="game-list">
                {sorted
                  .filter((game) => recent || groupKey(game.name) === group)
                  .map((game) => (
                    <button
                      key={game.project_id}
                      type="button"
                      className="game-row"
                      aria-label={game.name}
                      onClick={() => navigateContent(contentHash(game.project_id, 'info'))}
                    >
                      <GameAvatar game={game} />
                      <span className="row-text">
                        <Typography.Text className="game-name">{game.name}</Typography.Text>
                        <Typography.Text type="secondary" className="vn-game-time">
                          {date(game.last_played_at)}
                        </Typography.Text>
                      </span>
                      <Typography.Text type="secondary" className="version">
                        {game.release_version ?? t('ui.unknown')}
                      </Typography.Text>
                    </button>
                  ))}
              </div>
            </section>
          ))
        )}
        {state.loaded && !sorted.length && (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={t(
              state.games.length && filtered
                ? 'library.noMatchingGames'
                : recent
                  ? 'ui.noRecentlyPlayedGames'
                  : 'ui.yourLibraryIsEmptyAddAGameFromThe',
            )}
          >
            {filtered && state.games.length > 0 && (
              <Button onClick={() => updateFilters({ query: '', engine: 'all' })}>
                {t('library.clearFilters')}
              </Button>
            )}
          </Empty>
        )}
      </div>
    </div>
  );
}
