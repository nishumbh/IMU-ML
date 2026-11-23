/** Stable error codes shown to the user; the worker passes them across the message boundary. */
export type KbxErrorCode =
  | 'NOT_KBX' // magic is not KBX1
  | 'BAD_HEADER' // header fields violate section 4
  | 'UNSUPPORTED_VERSION' // format_version other than 1
  | 'PASSPHRASE_REQUIRED' // no passphrase and no key held in memory
  | 'WRONG_PASSPHRASE' // chunk 0 fails its tag: wrong passphrase or tampered data
  | 'CORRUPT_CHUNK' // a later chunk fails its tag
  | 'BAD_CHUNK' // chunk length out of range
  | 'BAD_RECORD' // a plaintext record does not parse
  | 'NO_WEBCRYPTO'
  | 'INTERNAL'; // unexpected error; details are not shown to avoid leaking data

export class KbxError extends Error {
  readonly code: KbxErrorCode;
  constructor(code: KbxErrorCode, message: string) {
    super(message);
    this.name = 'KbxError';
    this.code = code;
  }
}

export const USER_MESSAGES: Record<KbxErrorCode, string> = {
  NOT_KBX: 'This file is not a knee brace .kbx log.',
  BAD_HEADER: 'The .kbx header is invalid. The file may be damaged or from a different format.',
  UNSUPPORTED_VERSION: 'This .kbx file uses a format version this site does not read.',
  PASSPHRASE_REQUIRED: 'Enter the passphrase to decrypt these files.',
  WRONG_PASSPHRASE: 'Wrong passphrase, or the file was tampered with. No chunk could be verified with this key.',
  CORRUPT_CHUNK: 'The file is corrupted: a later chunk failed its integrity check. Data after that chunk is not shown.',
  BAD_CHUNK: 'A chunk has an impossible length. The file is damaged.',
  BAD_RECORD: 'A decrypted record could not be read. The file may be from incompatible firmware.',
  INTERNAL: 'Unexpected error while decrypting. Try again; if it persists, the file may be damaged.',
  NO_WEBCRYPTO: 'This browser does not provide Web Crypto. Use a current browser over HTTPS or localhost.',
};
