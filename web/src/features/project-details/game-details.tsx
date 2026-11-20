import { Alert, Button, Space, Tag, Typography } from 'antd';
import { Archive, Images, Info, Play, Square, Trash2 } from 'lucide-react';
import type { ReactNode } from 'react';
import type { Session } from '../../api/player.js';
import type { Project } from '../../api/types.js';
import type { ContentRoute } from '../../app/content-route.js';
import { bytes } from '../../ui/format.js';
import { GameAvatar } from '../../ui/game-avatar.js';
import { isKey, t } from '../../ui/preferences-store.js';
import { Segmented } from '../../ui/segments.js';
import { ProjectOverview } from './project-overview.js';

type DetailsActions = {
  changeTab: (tab: string) => void;
  chooseRuntime: (value: string) => void;
  play: () => void;
  remove: () => void;
  stop: () => void;
};

export function GameDetails({
  game,
  tab,
  independent,
  session,
  starting,
  runtimeChoice,
  actions,
  children,
}: {
  game: Project;
  tab: ContentRoute['tab'];
  independent: boolean;
  session: Session;
  starting: string | null;
  runtimeChoice: string;
  actions: DetailsActions;
  children: ReactNode;
}) {
  const sameRunning = session.state === 'running' && session.project_id === game.project_id;
  const runtimeId = runtimeChoice || game.recommendation.runtime_id;
  const runtimeAvailable = game.runtime_options.some(
    (runtime) => runtime.runtime_id === runtimeId && runtime.available,
  );
  const startDisabled =
    !!starting ||
    session.state === 'loading' ||
    session.state === 'recovering' ||
    !!game.deletion ||
    (!sameRunning && (!runtimeAvailable || !game.available || session.state === 'running'));
  const engineName =
    game.engine_name_key && isKey(game.engine_name_key)
      ? t(game.engine_name_key)
      : game.engine_display_name;
  const engineBadge = game.capabilities.uses_original_engine_version
    ? game.engine_version
      ? t('runtime.originalBadge', { version: game.engine_version })
      : t('runtime.unknownBadge')
    : engineName;
  const tabs = [
    { value: 'info', label: <TabLabel icon={<Info size={16} />} text={t('details.infoTab')} /> },
  ];
  if (game.capabilities.assets) {
    tabs.push({
      value: 'assets',
      label: <TabLabel icon={<Images size={16} />} text={t('assets.tab')} />,
    });
  }
  if (game.capabilities.save_management) {
    tabs.push({
      value: 'saves',
      label: <TabLabel icon={<Archive size={16} />} text={t('saves.tab')} />,
    });
  }

  return (
    <div id="details">
      <div className="hero" hidden={independent}>
        <GameAvatar game={game} large />
        <div>
          <Typography.Title level={2}>{game.name}</Typography.Title>
          <Typography.Text type="secondary" className="vn-game-version">
            {game.release_version ?? t('ui.unknownVersion')}
          </Typography.Text>
          <Space className="badges" wrap>
            <Tag>{engineBadge}</Tag>
            <Tag>{game.size_bytes === null ? t('ui.unknown') : bytes(game.size_bytes)}</Tag>
          </Space>
        </div>
      </div>
      <Segmented
        className="vn-details-tabs"
        hidden={independent}
        block
        value={tab}
        onChange={(value) => actions.changeTab(String(value))}
        options={tabs}
      />
      <div hidden={tab !== 'info'}>
        <ProjectOverview
          game={game}
          runtimeChoice={runtimeChoice}
          chooseRuntime={actions.chooseRuntime}
        />
        <Button
          danger
          className="delete-game"
          icon={<Trash2 size={16} />}
          disabled={
            game.deletion?.state === 'running' || session.state === 'recovering' || sameRunning
          }
          onClick={actions.remove}
        >
          {t(
            game.deletion?.state === 'failed'
              ? 'ui.retryDeletion'
              : game.deletion
                ? 'ui.deleting'
                : 'ui.deleteGame',
          )}
        </Button>
        {game.deletion?.error && (
          <Alert
            className="deletion-error"
            title={t(game.deletion.messages?.error ?? game.deletion.error)}
            type="error"
            showIcon
          />
        )}
      </div>
      {children}
      <div className="bottom-bar" hidden={independent}>
        {sameRunning && (
          <Button icon={<Square size={16} />} onClick={actions.stop}>
            {t('ui.stopGame')}
          </Button>
        )}
        <Button
          type="primary"
          size="large"
          icon={<Play size={16} />}
          disabled={startDisabled}
          onClick={actions.play}
        >
          {t(starting === game.project_id ? 'ui.starting' : sameRunning ? 'ui.resume' : 'ui.start')}
        </Button>
      </div>
    </div>
  );
}

function TabLabel({ icon, text }: { icon: ReactNode; text: string }) {
  return (
    <Space>
      {icon}
      {text}
    </Space>
  );
}
