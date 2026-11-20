export class Store<T> {
  private listeners = new Set<() => void>();
  constructor(protected data: T) {}
  getSnapshot = () => this.data;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  protected update(next: Partial<T>) {
    this.data = { ...this.data, ...next };
    for (const listener of this.listeners) listener();
  }
}
