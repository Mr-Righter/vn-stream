import { Progress } from 'antd';
import type { ReactNode } from 'react';

export function TaskProgress({
  percent,
  label,
  action,
  status,
  showInfo = true,
}: {
  percent: number;
  label?: string;
  action?: ReactNode;
  status?: 'normal' | 'active' | 'exception' | 'success';
  showInfo?: boolean;
}) {
  const value = Math.max(0, Math.min(100, Math.floor(percent)));
  return (
    <div className="vn-progress-row">
      <Progress percent={value} status={status} showInfo={false} aria-label={label} />
      {showInfo && (
        <span className="vn-progress-percent" aria-hidden="true">
          {value}%
        </span>
      )}
      {action}
    </div>
  );
}
