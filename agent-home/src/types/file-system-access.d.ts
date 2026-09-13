/**
 * Ambient types for the File System Access API (Folder Bridge,
 * `/files/bridge`). TypeScript's bundled `lib.dom.d.ts` doesn't yet include
 * `showDirectoryPicker` or the permission methods on `FileSystemHandle`, so
 * this fills exactly the surface `lib/folder-bridge/*` actually uses.
 *
 * Kept intentionally narrow (read-only, no `showOpenFilePicker` /
 * `showSaveFilePicker`, no write methods) to match this feature's v1 scope.
 */

interface FileSystemPermissionDescriptor {
  mode?: "read" | "readwrite";
}

interface FileSystemHandlePermissions {
  queryPermission(descriptor?: FileSystemPermissionDescriptor): Promise<PermissionState>;
  requestPermission(descriptor?: FileSystemPermissionDescriptor): Promise<PermissionState>;
}

interface FileSystemDirectoryHandle extends FileSystemHandlePermissions {
  readonly kind: "directory";
  readonly name: string;
  entries(): AsyncIterableIterator<[string, FileSystemDirectoryHandle | FileSystemFileHandle]>;
  getDirectoryHandle(name: string): Promise<FileSystemDirectoryHandle>;
  getFileHandle(name: string): Promise<FileSystemFileHandle>;
}

interface FileSystemFileHandle {
  readonly kind: "file";
  readonly name: string;
  getFile(): Promise<File>;
}

interface DirectoryPickerOptions {
  mode?: "read" | "readwrite";
}

interface Window {
  showDirectoryPicker(options?: DirectoryPickerOptions): Promise<FileSystemDirectoryHandle>;
}
