let guard: (() => Promise<boolean>) | null = null;
export function registerDraftGuard(next: () => Promise<boolean>) {
  guard = next;
  return () => {
    if (guard === next) guard = null;
  };
}
export function hasDraftGuard() {
  return guard !== null;
}
export async function allowDraftNavigation() {
  return !guard || (await guard());
}
export async function confirmDraftNavigation(action: () => void) {
  if (await allowDraftNavigation()) action();
}
