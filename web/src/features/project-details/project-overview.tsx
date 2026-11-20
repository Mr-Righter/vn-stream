import { Alert, Card, Descriptions, Select } from 'antd';
import type { Project } from '../../api/types.js';
import { bytes, date } from '../../ui/format.js';
import { t } from '../../ui/preferences-store.js';

export function ProjectOverview({
  game,
  runtimeChoice,
  chooseRuntime,
}: {
  game: Project;
  runtimeChoice: string;
  chooseRuntime: (value: string) => void;
}) {
  const usesOriginalVersion = game.capabilities.uses_original_engine_version;
  const runtimeLabel = t(usesOriginalVersion ? 'ui.engineVersion' : 'engine.runtime');
  const recommendation = game.recommendation;
  const recommendedName = usesOriginalVersion
    ? recommendation.build_version
    : (recommendation.display_name ?? recommendation.build_version);
  const unavailable = game.runtime_options.flatMap((option) =>
    !option.available && option.reason
      ? [
          <Alert
            key={option.runtime_id}
            type="info"
            showIcon
            title={option.display_name}
            description={t(option.reason)}
          />,
        ]
      : [],
  );
  const playTime =
    game.play_seconds === null
      ? t('ui.unavailable')
      : game.play_seconds
        ? t('library.playDuration', {
            minutes: Math.floor(game.play_seconds / 60),
            seconds: Math.floor(game.play_seconds % 60),
          })
        : t('ui.noRecords');

  return (
    <div className="vn-project-overview">
      <div className="vn-details-grid">
        <Card
          title={runtimeLabel}
          className="vn-engine-card"
          classNames={{ body: 'vn-runtime-controls' }}
        >
          <Select
            classNames={{ content: 'vn-select-value' }}
            id="runtime-version"
            aria-label={runtimeLabel}
            style={{ width: '100%' }}
            value={runtimeChoice}
            onChange={chooseRuntime}
            options={[
              {
                value: '',
                label: recommendedName
                  ? t('runtime.recommendedOption', { version: recommendedName })
                  : t('ui.chooseAVersion'),
                disabled: !recommendation.runtime_id,
              },
              ...game.runtime_options
                .filter((runtime) => runtime.runtime_id !== recommendation.runtime_id)
                .map((runtime) => ({
                  value: runtime.runtime_id,
                  disabled: !runtime.available,
                  label: usesOriginalVersion ? runtime.build_version : runtime.display_name,
                })),
            ]}
          />
          {unavailable.length > 0 && (
            <details className="vn-runtime-restrictions">
              <summary>{t('runtime.unavailableOptions')}</summary>
              <div className="vn-runtime-reasons">{unavailable}</div>
            </details>
          )}
        </Card>
        <Card title={t('ui.information')}>
          <Descriptions
            column={1}
            items={[
              {
                key: 'storage',
                label: t('ui.storage'),
                children: game.size_bytes === null ? t('ui.unknown') : bytes(game.size_bytes),
              },
              { key: 'last', label: t('ui.lastPlayed'), children: date(game.last_played_at) },
              { key: 'duration', label: t('ui.playTime'), children: playTime },
              { key: 'name', label: t('ui.gameName'), children: game.name },
              {
                key: 'version',
                label: t('ui.gameVersion'),
                children: game.release_version ?? t('ui.unknown'),
              },
              {
                key: 'path',
                label: t('ui.serverPath'),
                children: <span className="vn-file-path">{game.path}</span>,
              },
            ]}
          />
        </Card>
      </div>
      {!usesOriginalVersion &&
        game.recognition?.warnings.map((warning) => (
          <Alert key={warning} title={t(`engine.warning.${warning}`)} type="warning" showIcon />
        ))}
      {!usesOriginalVersion && !recommendation.runtime_id && !runtimeChoice && (
        <Alert title={t('engine.runtimeUnavailable')} type="warning" showIcon />
      )}
      {!game.available && (
        <Alert title={t('ui.gameFolderUnavailableCheckTheNasMount')} type="warning" showIcon />
      )}
    </div>
  );
}
