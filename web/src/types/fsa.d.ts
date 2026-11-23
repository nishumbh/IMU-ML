/** Minimal typings for the File System Access API (not in lib.dom for all TS versions). */
interface FsaFileHandle {
  kind: 'file';
  name: string;
  getFile(): Promise<File>;
}
interface FsaDirectoryHandle {
  kind: 'directory';
  name: string;
  values(): AsyncIterableIterator<FsaFileHandle | FsaDirectoryHandle>;
}
interface Window {
  showDirectoryPicker?: (options?: { mode?: 'read' }) => Promise<FsaDirectoryHandle>;
}
