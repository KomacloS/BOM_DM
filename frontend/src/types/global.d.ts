export interface DesktopBridgeApi {
  isElectron?: boolean;
  revealPath?: (absPath: string) => Promise<void> | void;
  saveText?: (content: string, defaultFileName?: string) => Promise<void> | void;
}

declare global {
  interface Window {
    desktopApi?: DesktopBridgeApi;
    api?: DesktopBridgeApi & {
      fetch?: typeof fetch;
    };
  }
}

export {};

