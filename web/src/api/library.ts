import { requestData, requestResource } from './client.js';
import type { StartRequest, Stream } from './generated/types.gen.js';
import type { Session } from './player.js';
import type { Engine, Project, ProjectDeletion } from './types.js';

export const listProjects = () => requestData<Project[]>('projects');
export const refreshProjects = () =>
  requestResource<Project[]>('projects/refresh', { method: 'POST' });
export const listEngines = () => requestData<Engine[]>('engines');
export const startProject = (projectId: string, runtimeId: string | null) => {
  const body: StartRequest = { runtime_id: runtimeId };
  return requestData<Session>(`projects/${encodeURIComponent(projectId)}/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
};
export const getStream = () => requestData<Stream>('stream');
export const deleteProject = (projectId: string) =>
  requestData<ProjectDeletion>(`projects/${encodeURIComponent(projectId)}`, { method: 'DELETE' });
export const getProjectDeletion = (taskId: string) =>
  requestData<ProjectDeletion>(`tasks/project-deletions/${encodeURIComponent(taskId)}`);
export const retryProjectDeletion = (taskId: string) =>
  requestData<ProjectDeletion>(`tasks/project-deletions/${encodeURIComponent(taskId)}/retry`, {
    method: 'POST',
  });
