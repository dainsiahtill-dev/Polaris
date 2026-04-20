/**
 * @deprecated Import from badge.tsx instead.
 * This file is kept for backward compatibility.
 *
 * Usage migration:
 *   OLD: import { StatusBadge } from '@/app/components/ui/status-badge';
 *   NEW: import { StatusBadge } from '@/app/components/ui/badge';
 *
 * Or use the new unified Badge API:
 *   import { Badge } from '@/app/components/ui/badge';
 *   <Badge variant="status:success">Online</Badge>
 *   <Badge variant="dot:success" pulse>Syncing</Badge>
 */

// Re-export all StatusBadge exports from badge.tsx
export {
  StatusBadge,
  Badge,
  badgeVariants,
  type BadgeVariant,
  type BadgeColor,
  type BadgeStyleVariant,
  type StatusBadgeColor,
  type StatusBadgeStyleVariant,
} from './badge';
