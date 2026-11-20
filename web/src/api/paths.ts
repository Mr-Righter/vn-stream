export const API_PREFIX = '/api/v2/';

export function apiURL(path: string): URL {
  return new URL(API_PREFIX + path, location.origin);
}
