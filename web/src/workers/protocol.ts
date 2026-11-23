import type { DecodedSession } from '../lib/types';
import type { KbxErrorCode } from '../lib/errors';

export interface DecryptRequest {
  type: 'decrypt';
  id: number;
  name: string;
  bytes: ArrayBuffer;
  /** Empty string means "use a key already held in worker memory for this salt". */
  passphrase: string;
  /** Keep the derived key in worker memory until forget or page close. */
  keepKey: boolean;
}

export interface ForgetRequest {
  type: 'forget';
  id: number;
}

export type WorkerRequest = DecryptRequest | ForgetRequest;

export type WorkerResponse =
  | { type: 'ok'; id: number; name: string; session: DecodedSession }
  | { type: 'error'; id: number; name: string; code: KbxErrorCode; message: string }
  | { type: 'forgotten'; id: number };
