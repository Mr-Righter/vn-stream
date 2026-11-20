import { Avatar } from 'antd';
import type { Project } from '../api/types.js';
export function GameAvatar({
  game,
  large = false,
}: {
  game: Pick<Project, 'icon_url' | 'name'>;
  large?: boolean;
}) {
  return (
    <Avatar
      className={`game-icon${large ? ' large' : ''}`}
      shape="square"
      size={large ? 96 : 48}
      src={game.icon_url ? new URL(game.icon_url, document.baseURI).href : undefined}
    >
      {game.name.slice(0, 1).toUpperCase()}
    </Avatar>
  );
}
