import type { LanceDbStatus } from '@/app/types/appContracts';

export function isLancedbExplicitlyBlocked(
  status: LanceDbStatus | null | undefined,
): boolean {
  return status?.ok === false;
}
